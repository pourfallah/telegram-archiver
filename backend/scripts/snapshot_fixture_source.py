"""Read-only live source snapshot of the RECOVERY_FINAL fixture from Account A.

Confirms each fixture message has REAL media (MessageMediaPhoto/Sticker/Audio/
Video/Document/WebPage) and captures reply/grouped/reaction fields — the
ground-truth source for the recovery.
"""
from __future__ import annotations
import asyncio, json
from pathlib import Path
import redis.asyncio as aioredis
from app.config import get_settings
from app.database import async_session_factory
from app.models import TelegramSession
from app.services.session_manager import SessionManager

A_SESSION_ID = 1
A_VIEW_PEER = 7768075024
MARK = "RECOVERY_FINAL_20260827_"


def _medsig(m):
    med = m.media
    if med is None:
        return None
    doc = getattr(med, "document", None)
    out = {"ctor": type(med).__name__}
    if doc is not None:
        out["doc_id"] = getattr(doc, "id", None)
        out["mime"] = getattr(doc, "mime_type", None)
        out["size"] = getattr(doc, "size", None)
        out["attrs"] = [type(a).__name__ for a in (getattr(doc, "attributes", None) or [])]
        for a in (getattr(doc, "attributes", None) or []):
            if hasattr(a, "file_name") and a.file_name:
                out["file_name"] = a.file_name
            if hasattr(a, "title"):
                out["title"] = getattr(a, "title", None)
            if hasattr(a, "performer"):
                out["performer"] = getattr(a, "performer", None)
            if hasattr(a, "duration"):
                out["duration"] = getattr(a, "duration", None)
            if hasattr(a, "alt"):
                out["alt"] = getattr(a, "alt", None)
            if hasattr(a, "w"):
                out["w"] = getattr(a, "w", None); out["h"] = getattr(a, "h", None)
    ph = getattr(med, "photo", None)
    if ph is not None:
        out["photo_id"] = getattr(ph, "id", None)
        out["sizes"] = [type(s).__name__ for s in (getattr(ph, "sizes", None) or [])]
    return out


async def main(out_path: str = "/data/e2e_source_snapshot.json"):
    settings = get_settings()
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    manager = SessionManager(settings=settings, redis=redis)
    async with async_session_factory() as db:
        from sqlalchemy import select
        acc = await db.scalar(select(TelegramSession).where(TelegramSession.id == A_SESSION_ID))
    client, release = await manager.acquire_client(acc)
    records = []
    try:
        me = await client.get_me()
        peer = await client.get_entity(A_VIEW_PEER)
        msgs = await client.get_messages(peer, limit=400)
        for m in msgs:
            # Capture the fixture window by id (computed from create_real_fixture
            # output above: ids 5676154..5676172) OR by marker text.
            if not (5676154 <= m.id <= 5676175):
                continue
            reply = getattr(m, "reply_to", None)
            reply_info = None
            if reply is not None:
                reply_info = {"reply_to_msg_id": getattr(reply, "reply_to_msg_id", None),
                              "top_id": getattr(reply, "reply_to_top_id", None)}
            rx = getattr(getattr(m, "reactions", None), "results", None)
            reactions = [{"emoji": getattr(getattr(r, "reaction", None), "emoticon", None),
                          "count": getattr(r, "count", 0),
                          "chosen": bool(getattr(r, "chosen", False))}
                         for r in (rx or [])]
            records.append({
                "id": m.id,
                "date": m.date.isoformat() if m.date else None,
                "text": (m.message or "")[:80],
                "sender_id": getattr(getattr(m, "sender", None), "id", None),
                "media": _medsig(m),
                "grouped_id": getattr(m, "grouped_id", None),
                "reply": reply_info,
                "reactions": reactions,
            })
        records.sort(key=lambda r: r["id"])
        (Path(out_path).write_text(json.dumps(records, ensure_ascii=False, indent=2)))
        # summary
        print("TOTAL FIXTURE MSGS:", len(records), "out:", out_path)
        for r in records:
            med = r["media"]
            m = f"{r['id']} | {('media=' + med['ctor']) if med else 'NO-MEDIA'}"
            if med and "Photo" in med["ctor"]:
                m += f"(photo_id={med.get('photo_id')})"
            if med and "Document" in med["ctor"]:
                m += f" {med.get('attrs')} {med.get('file_name','')}"
            m += f" | {r['text']!r}"
            if r["grouped_id"]:
                m += f" | grouped={r['grouped_id']}"
            if r["reply"]:
                m += f" | reply_to={r['reply']['reply_to_msg_id']}"
            if r["reactions"]:
                m += f" | rx={r['reactions']}"
            print(m)
    finally:
        await release()


asyncio.run(main())