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
    """Map source_message_id -> target message using multi-field keys
    (NEVER text-only, NEVER positional for media).

    Empty-text media messages are matched only when a target candidate with a
    COMPATIBLE media type exists (sticker->document w/ sticker attr,
    photo->photo, etc.). If no proof exists the source stays UNMATCHED:
    silently assigning it to another blank message can fake success.
    """
    mapping: dict[int, dict] = {}
    target_by_map: dict[str, dict] = {}
    target_by_text: dict[str, list[dict]] = {}
    for tgt in target_dicts:
        mk = _source_mapping_key(tgt)
        target_by_map.setdefault(mk, tgt)
        target_by_text.setdefault(_normalize_text(tgt.get("text") or ""), []).append(tgt)

    used_target_ids: set[int] = set()

    def _media_compat(src: dict, tgt: dict) -> bool:
        """Media-type compatibility proof between archive and live target."""
        src_types = {m.get("type") for m in (src.get("media") or [])}
        raw = tgt.get("target_media_raw") or {}
        ctor = raw.get("ctor") or ""
        attrs = raw.get("attrs") or []
        if "photo" in src_types:
            return ctor == "MessageMediaPhoto"
        if "sticker" in src_types:
            return ctor == "MessageMediaDocument"  # sticker arrives as document
        if "video" in src_types or "animation" in src_types or "gif" in src_types:
            return ctor == "MessageMediaDocument" and (
                "DocumentAttributeVideo" in attrs or "DocumentAttributeAnimated" in attrs)
        if "audio" in src_types or "voice" in src_types:
            return ctor == "MessageMediaDocument"
        if "document" in src_types:
            return ctor == "MessageMediaDocument"
        return False  # unknown/empty -> never positionally matched

    unmatched_src: list[tuple[dict, str]] = []
    for src in source_messages:
        sid = int(src.get("id") or 0)
        mk = _source_mapping_key(src)
        tgt = target_by_map.get(mk)
        match = "exact"
        if tgt is None or int(tgt.get("id") or 0) in used_target_ids:
            pending = target_by_text.get(_normalize_text(src.get("text") or ""))
            tgt = None
            if pending:
                compat = [t for t in pending
                          if int(t.get("id") or 0) not in used_target_ids
                          and ((src.get("media") and _media_compat(src, t))
                               or not src.get("media"))]
                if compat:
                    tgt = compat[0]
                    match = "text_only" if not src.get("media") else "text+media_type"
        if tgt is None or int(tgt.get("id") or 0) in used_target_ids:
            reason = "NO_MEDIA_TYPE_PROOF" if src.get("media") else "NO_TEXT_MATCH"
            unmatched_src.append((src, reason))
            continue
        used_target_ids.add(int(tgt.get("id") or 0))
        mapping[sid] = {
            "target_id": int(tgt.get("id")),
            "match": match,
            "source_text": (src.get("text") or "")[:60],
        }

    # NO positional fallback: unmatched sources stay unmapped (honest).
    if unmatched_src:
        logger.info(
            "Reconstruction mapping: %d source(s) left UNMATCHED (%s)",
            len(unmatched_src),
            ", ".join(f"{int(s.get('id') or 0)}:{reason}" for s, reason in unmatched_src),
        )
    return mapping


def unmatched_sources(
    source_messages: list[dict], mapping: dict[int, dict]
) -> list[dict]:
    """UNMATCHED_SOURCE_MEDIA records for reporting — never silently NO_MEDIA."""
    out = []
    for src in source_messages:
        sid = int(src.get("id") or 0)
        if sid in mapping:
            continue
        med = (src.get("media") or [{}])
        m0 = med[0] if isinstance(med, list) and med else {}
        out.append({
            "source_id": sid,
            "media_type": m0.get("type") if isinstance(m0, dict) else None,
            "reason": "NOT_MATERIALIZED",
        })
    return out


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
    session_resolver=None,
) -> list[dict]:
    """Execute SENDABLE reaction items via messages.sendReaction.

    Safety: if new_target_ids is provided, only those messages are touched.
    Never sends on behalf of a reactor whose session is not this client's.

    session_resolver: optional ``async fn(reactor_id) -> (client, release,
    view_peer)`` used to act AS the original reactor when that reactor is not
    the importing account (multi-account reconstruction). Message ids are
    per-participant in private chats, so the resolver's own view of the peer
    is used to translate target_id -> that participant's message id by text
    match against item["source_text"].
    """
    from telethon import functions
    from telethon import types as tl

    async def _send(send_client, send_peer, msg_id: int, payload) -> None:
        await send_client(functions.messages.SendReactionRequest(
            peer=send_peer, msg_id=msg_id, reaction=payload))

    results: list[dict] = []
    for item in plan:
        status = item.get("status")
        tid = item.get("target_id")
        if new_target_ids is not None and tid not in new_target_ids:
            item["outcome"] = "SKIPPED_NOT_NEW"
            results.append(item)
            continue

        # Build payload once
        if item["reaction_kind"] == "custom_emoji" and item.get("document_id"):
            payload = [tl.ReactionCustomEmoji(document_id=item["document_id"])]
        elif item.get("emoji"):
            payload = [tl.ReactionEmoji(emoticon=item["emoji"])]
        else:
            item["outcome"] = "FAILED_NO_REACTION_PAYLOAD"
            results.append(item)
            continue

        reactor = item.get("reactor_id")

        # Case 1: this client IS the reactor (importing account reacted).
        if status == "SENDABLE":
            try:
                await _send(client, peer, tid, payload)
                item["outcome"] = "RECONSTRUCTED_AFTER_IMPORT"
            except Exception as exc:  # noqa: BLE001
                name = type(exc).__name__
                if name == "MessageNotModifiedError":
                    item["outcome"] = "RECONSTRUCTED_ALREADY_PRESENT"
                else:
                    item["outcome"] = f"FAILED: {name}"
            results.append(item)
            continue

        # Case 2: another authenticated session exists for THIS reactor —
        # actually execute via that session (identity-correct), resolving the
        # per-participant message id in THAT session's view.
        if session_resolver is not None and reactor:
            acquired = None
            try:
                acquired = await session_resolver(int(reactor))
            except Exception as exc:  # noqa: BLE001
                logger.warning("session resolver failed for %s: %s", reactor, exc)
                acquired = None
            if acquired is not None:
                rx_client, rx_release, rx_peer = acquired
                try:
                    view_tid = await _resolve_view_message_id(
                        rx_client, rx_peer, item)
                    if view_tid is None:
                        item["outcome"] = "TARGET_NOT_IN_REACTOR_VIEW"
                    else:
                        try:
                            await _send(rx_client, rx_peer, view_tid, payload)
                            item["outcome"] = "RECONSTRUCTED_AFTER_IMPORT"
                            item["view_target_id"] = view_tid
                        except Exception as exc:  # noqa: BLE001
                            name = type(exc).__name__
                            if name == "MessageNotModifiedError":
                                item["outcome"] = "RECONSTRUCTED_ALREADY_PRESENT"
                                item["view_target_id"] = view_tid
                            else:
                                item["outcome"] = f"FAILED: {name}"
                finally:
                    await rx_release()
                results.append(item)
                continue

        # No way to act as this reactor — honest classification, never faked.
        item["outcome"] = status or "REACTOR_SESSION_REQUIRED"
        results.append(item)
    return results


async def _resolve_view_message_id(client, peer, item: dict) -> int | None:
    """Find the imported message in a given session's OWN view.

    Private-chat message ids differ per participant; match by normalized text
    (newest copy first wins for repeated imports).
    """
    want = _normalize_text(item.get("source_text") or "")
    if not want:
        return None
    msgs = await client.get_messages(peer, limit=80)
    best = None
    for m in msgs:
        if _normalize_text(m.message or "") == want:
            best = m.id  # keep scanning: get_messages returns newest-first
    return best


def classify_plan(plan: list[dict]) -> dict[str, int]:
    from collections import Counter

    return Counter(item.get("outcome") or item.get("status") for item in plan)
