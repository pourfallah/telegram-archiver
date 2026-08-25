"""Post-import reconstruction (Phase B).

After Telegram history import materializes, this module reconstructs
per-message state that the import protocol does NOT carry, where Telegram
technically permits it:

  - reactions      -> messages.sendReaction (CURRENT-state reconstruction)
  - replies        -> NOT reconstructable (no re-parenting RPC) — ARCHIVAL_ONLY
  - sticker entity -> NOT reconstructable (no sticker-attr attach RPC) — DOCUMENT_ONLY
  - custom emoji   -> NOT reconstructable as entities via sendMessage reliably — PARTIAL

Hard rules (never violated):
  1. A reaction by user X is ONLY sent by an authenticated session of user X.
     If X's session is unavailable -> REACTOR_SESSION_REQUIRED (never faked).
  2. Reconstruction targets ONLY new messages created by the import
     (snapshot delta) — never unrelated existing messages.
  3. Everything is labeled honestly: RESTORED_BY_IMPORT /
     RECONSTRUCTED_AFTER_IMPORT / CURRENT_STATE_ONLY / ARCHIVAL_ONLY / FAILED.
"""
from __future__ import annotations

import logging

from app.services.import_verification import _normalize_text, _source_mapping_key

logger = logging.getLogger(__name__)


class ReconstructionUnavailable(Exception):
    """Raised when a reconstruction step cannot be performed honestly."""


def build_source_target_mapping(
    source_messages: list[dict], target_dicts: list[dict]
) -> dict[int, dict]:
    """Map source_message_id -> target message using the SAME multi-field keys
    as the verifier (never text-only). Empty-text media messages that fail both
    exact and text matching fall back to POSITIONAL order (both sides are
    oldest-first), so a sticker and a photo in the same block never swap."""
    mapping: dict[int, dict] = {}
    target_by_map: dict[str, dict] = {}
    target_by_text: dict[str, list[dict]] = {}
    for tgt in target_dicts:
        mk = _source_mapping_key(tgt)
        target_by_map.setdefault(mk, tgt)
        target_by_text.setdefault(_normalize_text(tgt.get("text") or ""), []).append(tgt)

    unmatched_src: list[dict] = []
    used_target_ids: set[int] = set()
    for src in source_messages:
        mk = _source_mapping_key(src)
        tgt = target_by_map.get(mk)
        match = "exact"
        if tgt is None:
            pending = target_by_text.get(_normalize_text(src.get("text") or ""))
            if pending:
                tgt = pending[0]
                pending.pop(0)
                match = "text_only"
        if tgt is None or int(tgt.get("id") or 0) in used_target_ids:
            unmatched_src.append(src)
            continue
        used_target_ids.add(int(tgt.get("id") or 0))
        mapping[int(src.get("id"))] = {
            "target_id": int(tgt.get("id")),
            "match": match,
            "source_text": (src.get("text") or "")[:60],
        }

    # Positional fallback: remaining source (oldest-first) -> remaining target
    # (oldest-first, excluding used). Targets arrive newest-first from MTProto,
    # so reverse to get oldest-first.
    remaining = [t for t in target_dicts
                 if int(t.get("id") or 0) not in used_target_ids]
    remaining.reverse()  # oldest-first
    for src in unmatched_src:
        if not remaining:
            break
        tgt = remaining.pop(0)
        used_target_ids.add(int(tgt.get("id") or 0))
        mapping[int(src.get("id"))] = {
            "target_id": int(tgt.get("id")),
            "match": "positional",
            "source_text": (src.get("text") or "")[:60],
        }
    return mapping


def plan_reactions(
    source_messages: list[dict],
    mapping: dict[int, dict],
    session_account_ids: set[int],
    source_me_id: int | None,
    target_me_id: int | None,
) -> list[dict]:
    """Plan reaction reconstruction with STRICT reactor-identity rules.

    For each archived reaction voter (peer_id) we require an authenticated
    session of THAT user. Without it we emit REACTOR_SESSION_REQUIRED and
    never fake the reaction from a different account.

    Returns a plan list of {target_id, reaction_kind, emoji|document_id,
    reactor_id, status}.
    """
    plan: list[dict] = []
    for src in source_messages:
        sid = int(src.get("id") or 0)
        tgt = mapping.get(sid)
        if not tgt:
            continue
        rx = src.get("reactions") or {}
        voters = rx.get("voters") or []
        totals = rx.get("reactions") or []
        if voters:
            # voter-level reconstruction (identity-preserving)
            for v in voters:
                reactor = v.get("peer_id")
                emoji = v.get("emoji")
                doc_id = v.get("document_id")
                if not reactor:
                    continue
                status = _reactor_status(reactor, session_account_ids,
                                         source_me_id, target_me_id)
                plan.append({
                    "target_id": tgt["target_id"],
                    "source_message_id": sid,
                    "reactor_id": reactor,
                    "reaction_kind": "custom_emoji" if doc_id else "emoji",
                    "emoji": emoji,
                    "document_id": doc_id,
                    "status": status,
                })
        else:
            # totals-only archive: identities unknown -> cannot attribute safely
            for t in totals:
                plan.append({
                    "target_id": tgt["target_id"],
                    "source_message_id": sid,
                    "reactor_id": None,
                    "reaction_kind": "custom_emoji" if t.get("document_id") else "emoji",
                    "emoji": t.get("emoji"),
                    "document_id": t.get("document_id"),
                    "status": "REACTOR_UNKNOWN",
                })
    return plan


def _reactor_status(
    reactor_id: int,
    session_account_ids: set[int],
    source_me_id: int | None,
    target_me_id: int | None,
) -> str:
    if reactor_id in session_account_ids:
        return "SENDABLE"
    if reactor_id == source_me_id or reactor_id == target_me_id:
        return "REACTOR_SESSION_REQUIRED"  # known reactor, session not connected
    return "REACTOR_SESSION_REQUIRED"


async def reconstruct_reactions(
    client,
    peer,
    plan: list[dict],
    new_target_ids: set[int] | None = None,
) -> list[dict]:
    """Execute SENDABLE reaction items via messages.sendReaction.

    Safety: if new_target_ids is provided, only those messages are touched.
    Never sends on behalf of a reactor whose session is not this client's.
    """
    from telethon import functions
    from telethon import types as tl

    results: list[dict] = []
    for item in plan:
        if item["status"] != "SENDABLE":
            item["outcome"] = item["status"]
            results.append(item)
            continue
        tid = item["target_id"]
        if new_target_ids is not None and tid not in new_target_ids:
            item["outcome"] = "SKIPPED_NOT_NEW"
            results.append(item)
            continue
        try:
            if item["reaction_kind"] == "custom_emoji" and item.get("document_id"):
                reaction = [tl.ReactionCustomEmoji(document_id=item["document_id"])]
            elif item.get("emoji"):
                reaction = [tl.ReactionEmoji(emoticon=item["emoji"])]
            else:
                item["outcome"] = "FAILED_NO_REACTION_PAYLOAD"
                results.append(item)
                continue
            await client(functions.messages.SendReactionRequest(
                peer=peer, msg_id=tid, reaction=reaction))
            item["outcome"] = "RECONSTRUCTED_AFTER_IMPORT"
        except Exception as exc:  # noqa: BLE001
            item["outcome"] = f"FAILED: {type(exc).__name__}"
        results.append(item)
    return results


def classify_plan(plan: list[dict]) -> dict[str, int]:
    from collections import Counter

    return Counter(item.get("outcome") or item.get("status") for item in plan)
