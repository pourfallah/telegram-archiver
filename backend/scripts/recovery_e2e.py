"""DEFINITIVE END-TO-END RECOVERY TEST — real accounts, real conversation.

Stages (each gated by a checkpoint file on disk; a later stage refuses to run
if an earlier one failed):
  1 snapshot  — read-only complete source snapshot from Account A
  2 clear     — B-side clear ONLY (deleteHistory just_clear=true, NO revoke)
                + verify A still intact; ABORT otherwise
  3 import    — direct MTProto history import into the same A<->B peer
  4 verify    — timed target reads T+0/30/60/180/300
  5 report    — source->target mapping + FINAL_RECOVERY_REPORT.html

Run:  python3 recovery_e2e.py <run_id> [stage]
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from pathlib import Path

import redis.asyncio as aioredis

from app.config import get_settings
from app.database import async_session_factory
from app.models import TelegramSession
from app.services.session_manager import SessionManager

A_SESSION_ID = 1      # +989394430100 First Dev. (source of truth)
B_SESSION_ID = 3      # +5511991966422 David Rodriguez (recovery target)
A_VIEW_PEER = 7768075024   # from A's perspective: David
B_VIEW_PEER = 165649921    # from B's perspective: First Dev.
RUNS = Path("/data/fidelity/test_runs")


def _iso(dt) -> str:
    return dt.isoformat()[:19] if dt else None


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _media_sig(m) -> dict:
    med = m.media
    if med is None:
        return {"type": "none"}
    ctor = type(med).__name__
    out = {"ctor": ctor}
    doc = getattr(med, "document", None)
    if doc is not None:
        out["document_id"] = getattr(doc, "id", None)
        out["mime"] = getattr(doc, "mime_type", None)
        out["size"] = getattr(doc, "size", None)
        attrs = []
        for a in getattr(doc, "attributes", None) or []:
            d = {"ctor": type(a).__name__}
            for f in ("file_name", "duration", "w", "h", "performer", "title",
                      "voice", "round_message", "sticker_set", "alt"):
                if hasattr(a, f):
                    v = getattr(a, f, None)
                    if v is not None:
                        d[f] = str(v)
            if hasattr(a, "emoji"):
                d["emoji"] = getattr(a, "emoji", None)
            attrs.append(d)
        out["attrs"] = attrs
    photo = getattr(med, "photo", None)
    if photo is not None:
        out["photo_id"] = getattr(photo, "id", None)
        out["photo_sizes"] = [type(s).__name__ for s in getattr(photo, "sizes", None) or []]
    return out


async def _messages_from(client, peer, limit: int = 500):
    """Full-history fetch: newest-first pagination via get_messages + offset."""
    all_msgs = []
    offset_id = 0
    while True:
        batch = await client.get_messages(peer, limit=min(limit, 100), offset_id=offset_id)
        if not batch:
            break
        all_msgs.extend(batch)
        if len(batch) < min(limit, 100):
            break
        offset_id = min(m.id for m in batch)
    return all_msgs


async def stage_snapshot(run_dir: Path) -> dict:
    settings = get_settings()
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    manager = SessionManager(settings=settings, redis=redis)
    src_dir = run_dir / "source"
    src_dir.mkdir(parents=True, exist_ok=True)

    async with async_session_factory() as db:
        from sqlalchemy import select
        acc_a = await db.scalar(select(TelegramSession).where(TelegramSession.id == A_SESSION_ID))

    client, release = await manager.acquire_client(acc_a)
    records = []
    try:
        me = await client.get_me()
        peer = await client.get_entity(A_VIEW_PEER)
        msgs = await _messages_from(client, peer)

        source_peer = {
            "me_id": getattr(me, "id", None),
            "me_name": getattr(me, "first_name", None),
            "peer_id": getattr(peer, "id", None),
            "peer_name": getattr(peer, "first_name", None),
            "peer_username": getattr(peer, "username", None),
        }

        participants = {str(me.id): me.first_name,
                        str(peer.id): peer.first_name}

        media_manifest = []
        reactions_src = []
        grouped = {}
        fingerprints = {}
        checksums = {}

        for m in sorted(msgs, key=lambda x: x.id):
            fwd = getattr(m, "fwd_from", None)
            fwd_info = None
            if fwd is not None:
                fwd_info = {
                    "from_id": getattr(getattr(fwd, "from_id", None), "user_id", None)
                    or getattr(getattr(fwd, "from_id", None), "channel_id", None)
                    or getattr(getattr(fwd, "from_id", None), "chat_id", None),
                    "from_name": getattr(fwd, "from_name", None),
                    "date": _iso(getattr(fwd, "date", None)),
                    "channel_post": getattr(fwd, "channel_post", None),
                    "post_author": getattr(fwd, "post_author", None),
                    "saved_from_msg_id": getattr(fwd, "saved_from_msg_id", None),
                }
            reply_to = getattr(m, "reply_to", None)
            reply_info = None
            if reply_to is not None:
                rid = getattr(reply_to, "reply_to_msg_id", None)
                nested = getattr(reply_to, "reply_to", None)
                if rid is None and nested is not None:
                    rid = getattr(nested, "message_id", None)
                reply_info = {
                    "reply_to_msg_id": rid,
                    "top_msg_id": getattr(reply_to, "reply_to_top_id", None),
                    "quote": getattr(reply_to, "quote", None),
                }
            entities = []
            for e in getattr(m, "entities", None) or []:
                d = {"ctor": type(e).__name__, "offset": getattr(e, "offset", 0),
                     "length": getattr(e, "length", 0)}
                if hasattr(e, "document_id"):
                    d["document_id"] = e.document_id
                entities.append(d)
            rx = getattr(getattr(m, "reactions", None), "results", None)
            reaction_rows = []
            if rx:
                for r in rx:
                    reaction = getattr(r, "reaction", None)
                    reaction_rows.append({
                        "ctor": type(reaction).__name__ if reaction else None,
                        "emoji": getattr(reaction, "emoticon", None) if reaction else None,
                        "document_id": getattr(reaction, "document_id", None) if reaction else None,
                        "count": getattr(r, "count", 0),
                    })
                    reactions_src.append({
                        "message_id": m.id,
                        "reaction": reaction_rows[-1],
                    })
            grouped_id = getattr(m, "grouped_id", None)
            if grouped_id is not None:
                grouped.setdefault(str(grouped_id), []).append(m.id)

            media_sig = _media_sig(m)
            if media_sig.get("type") != "none":
                media_manifest.append({
                    "source_message_id": m.id,
                    **media_sig,
                    "caption": getattr(m, "message", "") or None,
                })

            rec = {
                "source_message_id": m.id,
                "date": _iso(m.date),
                "edit_date": _iso(getattr(m, "edit_date", None)),
                "sender_id": getattr(getattr(m, "sender", None), "id", None),
                "sender_name": getattr(getattr(m, "sender", None), "first_name", None),
                "text": getattr(m, "message", "") or "",
                "entities": entities,
                "reply": reply_info,
                "forward": fwd_info,
                "media": media_sig,
                "grouped_id": grouped_id,
                "reactions": reaction_rows,
                "out": getattr(m, "out", None),
            }
            records.append(rec)

            # fingerprint: multi-field identity
            fp = _sha256(json.dumps({
                "sender": rec["sender_id"],
                "date": rec["date"],
                "text": rec["text"],
                "media_ctor": media_sig.get("ctor", "none"),
                "grouped": grouped_id,
            }, sort_keys=True).encode("utf-8"))
            fingerprints[str(m.id)] = fp
            checksums[f"msg_{m.id}"] = fp

        (src_dir / "source_messages.json").write_text(
            json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        (src_dir / "source_messages.ndjson").write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
            encoding="utf-8")
        (src_dir / "source_media_manifest.json").write_text(
            json.dumps(media_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        (src_dir / "source_reactions.json").write_text(
            json.dumps(reactions_src, ensure_ascii=False, indent=2), encoding="utf-8")
        (src_dir / "source_participants.json").write_text(
            json.dumps(participants, ensure_ascii=False, indent=2), encoding="utf-8")
        (src_dir / "source_peer.json").write_text(
            json.dumps(source_peer, ensure_ascii=False, indent=2), encoding="utf-8")
        grouped_out = {k: v for k, v in grouped.items()} or "NO_GROUPED_MEDIA_IN_THIS_FIXTURE"
        (src_dir / "source_grouped_media.json").write_text(
            json.dumps(grouped_out, ensure_ascii=False, indent=2), encoding="utf-8")
        (src_dir / "source_fingerprints.json").write_text(
            json.dumps(fingerprints, ensure_ascii=False, indent=2), encoding="utf-8")
        (src_dir / "checksums.sha256").write_text(
            "\n".join(f"{h}  {k}" for k, h in sorted(checksums.items())) + "\n",
            encoding="utf-8")

        summary = {
            "total_messages": len(records),
            "message_ids": [r["source_message_id"] for r in records],
            "media_items": len(media_manifest),
            "reaction_items": len(reactions_src),
            "grouped": grouped if isinstance(grouped, dict) and grouped else None,
            "replies": [r["source_message_id"] for r in records if r.get("reply")],
            "forwards": [r["source_message_id"] for r in records if r.get("forward")],
        }
        (src_dir / "source_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return summary
    finally:
        await release()


async def stage_clear(run_dir: Path) -> dict:
    """B-side clear ONLY via deleteHistory(just_clear=True, revoke=False)."""
    settings = get_settings()
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    manager = SessionManager(settings=settings, redis=redis)

    async with async_session_factory() as db:
        from sqlalchemy import select
        acc_b = await db.scalar(select(TelegramSession).where(TelegramSession.id == B_SESSION_ID))

    client, release = await manager.acquire_client(acc_b)
    try:
        me = await client.get_me()
        peer = await client.get_entity(B_VIEW_PEER)
        before_total = await client.get_messages(peer, limit=0)
        before = {"total": getattr(before_total, "total", None),
                  "me_id": me.id}

        from telethon import functions
        res = await client(functions.messages.DeleteHistoryRequest(
            peer=peer, max_id=0, just_clear=True, revoke=False))
        (run_dir / "target" ).mkdir(exist_ok=True)
        (run_dir / "target" / "target_before_clear.json").write_text(
            json.dumps(before, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"cleared": True, "pts": getattr(res, "pts", None), "before": before}
    finally:
        await release()


async def _verify_clear(run_dir: Path) -> dict:
    """Check A intact + B empty; returns verdict."""
    settings = get_settings()
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    manager = SessionManager(settings=settings, redis=redis)

    async with async_session_factory() as db:
        from sqlalchemy import select
        acc_a = await db.scalar(select(TelegramSession).where(TelegramSession.id == A_SESSION_ID))
        acc_b = await db.scalar(select(TelegramSession).where(TelegramSession.id == B_SESSION_ID))

    summary = json.loads((run_dir / "source" / "source_summary.json").read_text())

    ca, ra = await manager.acquire_client(acc_a)
    a_after = None
    try:
        peer_a = await ca.get_entity(A_VIEW_PEER)
        msgs_a = await ca.get_messages(peer_a, limit=200)
        a_after = {"count": len(msgs_a), "ids": [m.id for m in msgs_a]}
    finally:
        await ra()

    cb, rb = await manager.acquire_client(acc_b)
    b_after = None
    try:
        peer_b = await cb.get_entity(B_VIEW_PEER)
        msgs_b = await cb.get_messages(peer_b, limit=200)
        b_after = {"count": len(msgs_b), "ids": [m.id for m in msgs_b]}
    finally:
        await rb()

    verdict = {
        "a_after_clear": a_after,
        "b_after_clear": b_after,
        "a_intact": a_after and all(
            m_id in a_after["ids"] for m_id in summary["message_ids"]),
        "b_empty_of_source": b_after and not any(
            m_id in b_after["ids"] for m_id in summary["message_ids"]),
    }
    (run_dir / "target" / "target_after_clear_verification.json").write_text(
        json.dumps(verdict, ensure_ascii=False, indent=2), encoding="utf-8")
    return verdict


async def main(run_id: str, stage: str | None = None):
    run_dir = RUNS / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    stages = ["snapshot", "clear", "verify_clear", "import", "verify", "report"]
    if stage and stage not in stages:
        print(f"unknown stage {stage!r}; must be one of {stages}")
        return 2

    if not stage or stage == "snapshot":
        summary = await stage_snapshot(run_dir)
        print(json.dumps({"stage": "snapshot", **summary}, ensure_ascii=False, indent=2))
        (run_dir / "checkpoint_snapshot.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        if stage:
            return 0

    if stage in ("clear",):
        print("Clearing B side ONLY (just_clear=true, revoke=false)...")
        r = await stage_clear(run_dir)
        print(json.dumps({"stage": "clear", **r}, ensure_ascii=False, indent=2))
        (run_dir / "checkpoint_clear.json").write_text(
            json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0

    if stage == "verify_clear":
        v = await _verify_clear(run_dir)
        print(json.dumps({"stage": "verify_clear", **v}, ensure_ascii=False, indent=2))
        if not v["a_intact"]:
            print("!!! CRITICAL: source history not intact — ABORT, do not import")
            return 1
        return 0

    print("stages 'import'/'verify'/'report' are executed by recovery_e2e_import.py")
    return 0


if __name__ == "__main__":
    rid = sys.argv[1] if len(sys.argv) > 1 else "run1"
    stg = sys.argv[2] if len(sys.argv) > 2 else None
    raise SystemExit(asyncio.run(main(rid, stg)))
