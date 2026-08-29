"""Reaction reconstruction: same reactor, same reaction, same target message.

Uses messages.sendReaction from EACH reactor's own session — never
cross-account. Reaction dates are not preserved (not required).
Verification reads target reactions via messages.getMessagesReactions.
"""

from __future__ import annotations

import json
from pathlib import Path

from telethon import functions, types
from telethon.errors import MessageNotModifiedError, ReactionInvalidError

from .telegram_client import ClientPool

EMOJI_REACTION = types.ReactionEmoji


def parse_reaction(r: dict) -> types.Reaction:
    """Build a TL Reaction from an archived reaction JSON record."""
    # Archived shape: {"reaction": {"_": "ReactionEmoji", "emoticon": "👍"}, ...}
    inner = r["reaction"]
    if inner.get("_") == "ReactionCustomEmoji":
        return types.ReactionCustomEmoji(document_id=inner["document_id"])
    return types.ReactionEmoji(emoticon=inner.get("emoticon", "👍"))


class ReactionReconstructor:
    def __init__(self, pool: ClientPool, peer, run_dir: Path) -> None:
        self.pool = pool
        self.peer = peer  # target peer (same for both sessions; A<->B chat)
        self.run_dir = run_dir

    async def reconstruct(self, archive_dir: Path, mapping: dict) -> dict:
        """Send archived reactions from the correct reactor sessions.

        The archive records WHO reacted only via sender-relative data, so the
        reactions.ndjson entries carry source_message_id + reaction + count.
        We additionally read the source reaction list per message to identify
        the reactor (A or B) before import. Here we replay from per-reactor
        plan files produced during export (reactions_plan.json).
        """
        plan_path = archive_dir / "reactions_plan.json"
        if not plan_path.exists():
            return {"planned": 0, "sent": 0, "results": []}
        plan = json.loads(plan_path.read_text())
        id_map = {m["source_message_id"]: m["target_message_id"] for m in mapping["mappings"]}
        results = []
        sent = 0
        for item in plan:
            src_id = item["source_message_id"]
            tgt_id = id_map.get(src_id)
            reactor = item["reactor"]  # "A" or "B"
            reaction = parse_reaction(item)
            entry = {
                "source_message_id": src_id,
                "target_message_id": tgt_id,
                "reactor": reactor,
                "reaction": item["reaction"],
                "status": "SKIPPED_NO_TARGET" if tgt_id is None else "PENDING",
            }
            if tgt_id is not None:
                try:
                    client = self.pool.client(reactor)
                    msg_id = self._own_view_msg_id(reactor, tgt_id)
                    await client(
                        functions.messages.SendReactionRequest(
                            peer=self.peer,
                            msg_id=msg_id,
                            reaction=[reaction],
                        )
                    )
                    entry["status"] = "SENT"
                    sent += 1
                except MessageNotModifiedError:
                    entry["status"] = "ALREADY_PRESENT"
                    sent += 1
                except ReactionInvalidError as e:
                    entry["status"] = "REJECTED"
                    entry["error"] = str(e)
                except Exception as e:  # noqa: BLE001 - record and continue
                    entry["status"] = "FAILED"
                    entry["error"] = str(e)
            results.append(entry)
        out = {"planned": len(plan), "sent": sent, "results": results}
        (self.run_dir / "reactions_reconstruction.json").write_text(json.dumps(out, indent=2))
        return out

    def _own_view_msg_id(self, reactor: str, target_id: int) -> int:
        """A and B see DIFFERENT message ids for the same message.

        The mapping stores B's view (target ids were read from B). For reactor
        A, resolve the same physical message from A's own view via the
        mapping's pair table when available; fall back to the target id.
        """
        pair_path = self.run_dir / "target_message_pairs.json"
        if pair_path.exists() and reactor == "A":
            pairs = json.loads(pair_path.read_text())
            return pairs.get(str(target_id), target_id)
        return target_id


async def verify_reactions(pool: ClientPool, peer, run_dir: Path, mapping: dict) -> dict:
    """Read target reactions via getMessagesReactions and compare with plan."""
    plan_path = run_dir / "archive" / "reactions_plan.json"
    recon_path = run_dir / "reactions_reconstruction.json"
    if not plan_path.exists() or not recon_path.exists():
        return {"status": "NO_REACTIONS_ARCHIVED"}
    plan = {p["source_message_id"]: p for p in json.loads(plan_path.read_text())}
    recon = json.loads(recon_path.read_text())
    id_map = {m["source_message_id"]: m["target_message_id"] for m in mapping["mappings"]}

    client = pool.client("B")
    checks = []
    for r in recon["results"]:
        tgt = r.get("target_message_id")
        if r["status"] not in ("SENT", "ALREADY_PRESENT") or tgt is None:
            checks.append({**r, "verified": False, "reason": r["status"]})
            continue
        res = await client(
            functions.messages.GetMessagesReactionsRequest(
                peer=peer, id=[tgt]
            )
        )
        # Find the updates' reaction results for this message
        verified = False
        seen = []
        for upd in getattr(res, "updates", []):
            for m in getattr(upd, "messages", []):
                rx = getattr(m, "reactions", None)
                if rx:
                    for rc in rx.results:
                        emoticon = getattr(rc.reaction, "emoticon", None)
                        doc_id = getattr(rc.reaction, "document_id", None)
                        seen.append({"emoticon": emoticon, "document_id": doc_id, "count": rc.count})
                        expected = plan.get(r["source_message_id"], {}).get("reaction", {})
                        want_emoji = expected.get("reaction", {}).get("emoticon")
                        if (want_emoji and emoticon == want_emoji) or (
                            expected.get("reaction", {}).get("_") == "ReactionCustomEmoji"
                            and doc_id == expected["reaction"].get("document_id")
                        ):
                            verified = True
        checks.append({**r, "verified": verified, "seen": seen})
    out = {"checks": checks, "all_verified": all(c["verified"] for c in checks)}
    (run_dir / "reactions_verification.json").write_text(json.dumps(out, indent=2, default=str))
    return out
