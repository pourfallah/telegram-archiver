"""Reaction handling for Telegram Recovery v2.

Reaction fidelity is about WHO reacted, WITH WHAT, ON WHICH message — not the
reaction timestamp. We archive reactor identities per source message, then
reconstruct each reaction with the correct actor's session (A reacts with A's
session, B with B's), then verify on the target with ``getMessagesReactions``.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from telethon.tl import functions as tg_functions
from telethon.tl import types as tl_types

logger = logging.getLogger("recovery.reactions")

REACTION_CLASSES = {
    "ReactionEmoji": ("emoticon",),
    "ReactionCustomEmoji": ("document_id",),
    "ReactionPaid": (),
    "ReactionEmpty": (),
    "ReactionCount": ("count", "chosen"),  # not a sendable reaction
}

MAX_REACTOR_LIST = 100


class ReactionStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.data: dict[int, list[dict[str, Any]]] = {}
        if self.path.exists():
            self.data = json.loads(self.path.read_text(encoding="utf-8"))

    def add(self, source_message_id: int, reactor_id: int | None,
            reaction: dict[str, Any], chosen: bool = False) -> None:
        self.data.setdefault(source_message_id, []).append({
            "reactor_id": reactor_id,
            "reaction": reaction,
            "chosen": chosen,
        })

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2),
                             encoding="utf-8")


def classify_reaction(reaction: Any) -> dict[str, Any]:
    """Serialize a ``Reaction`` TL object to a plain, resendable dict."""
    name = type(reaction).__name__
    out = {"__tl__": name}
    if name == "ReactionEmoji":
        out["emoticon"] = getattr(reaction, "emoticon", None)
    elif name == "ReactionCustomEmoji":
        out["document_id"] = getattr(reaction, "document_id", None)
    return out


def reaction_to_tl(plain: dict[str, Any]) -> Any:
    """Rebuild a ``Reaction`` TL object from its archived plain form."""
    name = plain.get("__tl__", "ReactionEmoji")
    if name == "ReactionCustomEmoji":
        return tl_types.ReactionCustomEmoji(document_id=plain["document_id"])
    emoticon = plain.get("emoticon") or "👍"
    return tl_types.ReactionEmoji(emoticon=emoticon)


async def archive_reactions(client, peer, archive,
                            reacted_by: dict[int, str] | None = None) -> dict[str, int]:
    """Fetch per-message reactors via ``getMessageReactionsList`` and store them.

    ``reacted_by``: optional mapping source_message_id -> actor label (not
    required for the archive itself, which stores raw reactor peer ids).
    Returns {"messages": N, "reaction_entries": M}.
    """
    store = ReactionStore(archive.reactions_dir / "archive.json")
    messages = 0
    entries = 0
    for rec in archive.read_messages():
        reactions = rec.get("reactions") or {}
        if not reactions.get("rows"):
            continue
        messages += 1
        try:
            res = await client.call(tg_functions.messages.GetMessageReactionsListRequest(
                peer=peer, id=rec["source_message_id"], limit=MAX_REACTOR_LIST,
                reaction=None, offset=""))
        except Exception as exc:  # noqa: BLE001
            logger.warning("reactor list failed for msg %s: %s", rec["source_message_id"], exc)
            continue
        for r in getattr(res, "reactions", None) or []:
            reactor = getattr(r, "peer_id", None)
            reaction = classify_reaction(getattr(r, "reaction", None))
            reactor_id = _peer_id(reactor)
            store.add(rec["source_message_id"], reactor_id, reaction,
                      chosen=bool(getattr(r, "big", False)))
            entries += 1
    store.save()
    return {"messages": messages, "reaction_entries": entries}


def _peer_id(p) -> int | None:
    if p is None:
        return None
    for name in ("user_id", "channel_id", "chat_id"):
        v = getattr(p, name, None)
        if v is not None:
            return v
    return getattr(p, "id", None)


async def reconstruct_reactions(target_client, peer, archive, mapping,
                                actor_sessions: dict[str, Any],
                                import_id_state=None) -> list[dict[str, Any]]:
    """Re-send each archived reaction on the target using the correct actor.

    ``actor_sessions`` maps actor id -> the ``RecoveryClient`` that reacts as
    that actor (so A's reactions are sent by A, B's by B — never impersonated).
    Uses the archived reactor identity to pick the session.
    """
    store = ReactionStore(archive.reactions_dir / "archive.json")
    by_target = {}
    for m in mapping:
        by_target[m.source_message_id] = m.target_message_id

    applied: list[dict[str, Any]] = []
    for source_msg_id, reactions in store.data.items():
        source_msg_id = int(source_msg_id)  # JSON keys round-trip as strings
        target_msg_id = by_target.get(source_msg_id)
        if target_msg_id is None or target_msg_id < 0:
            applied.append({"source": source_msg_id, "target": None,
                            "status": "SKIPPED_UNMAPPED"})
            continue
        for r in reactions:
            reactor_id = r.get("reactor_id")
            session = actor_sessions.get(str(reactor_id))
            if session is None:
                applied.append({"source": source_msg_id, "target": target_msg_id,
                                "reaction": r["reaction"],
                                "status": "NO_SESSION_FOR_REACTOR"})
                continue
            try:
                await session.call(tg_functions.messages.SendReactionRequest(
                    peer=peer, msg_id=target_msg_id, big=False, add_to_recent=True,
                    reaction=[reaction_to_tl(r["reaction"])]))
                applied.append({"source": source_msg_id, "target": target_msg_id,
                                "reaction": r["reaction"],
                                "status": "RECONSTRUCTED"})
            except Exception as exc:  # noqa: BLE001
                applied.append({"source": source_msg_id, "target": target_msg_id,
                                "reaction": r["reaction"],
                                "status": "FAILED",
                                "error": f"{type(exc).__name__}: {exc}"})
    return applied


async def verify_reactions(target_client, peer, target_message_ids: list[int]) -> dict[str, Any]:
    """Read target reactions with ``getMessagesReactions`` (bulk counts)."""
    if not target_message_ids:
        return {"checked": 0}
    res = await target_client.call(tg_functions.messages.GetMessagesReactionsRequest(
        peer=peer, id=target_message_ids))
    result = {}
    for m in getattr(res, "updates", None) or []:
        if isinstance(m, tl_types.UpdateMessageReactions):
            mid = int(m.msg_id)
            rows = []
            for rc in getattr(getattr(m, "reactions", None), "results", None) or []:
                rows.append({"reaction": classify_reaction(getattr(rc, "reaction", None)),
                             "count": getattr(rc, "count", 0),
                             "chosen": bool(getattr(rc, "chosen", False))})
            result[mid] = rows
    return {"checked": len(target_message_ids), "messages": result}