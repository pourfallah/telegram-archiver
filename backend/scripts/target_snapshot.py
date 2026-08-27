"""Independent target read from Account B — verify real media on imported fixture."""
from __future__ import annotations
import asyncio, json
from pathlib import Path
import redis.asyncio as aioredis
from app.config import get_settings
from app.database import async_session_factory
from app.models import TelegramSession
from app.services.session_manager import SessionManager

B_SESSION_ID = 3
B_VIEW_PEER = 165649921
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
        out["attrs"] = [type(a).__name__ for a in (getattr(doc, "attributes", None) or [])]
        for a in (getattr(doc, "attributes", None) or []):
            if hasattr(a, "file_name") and a.file_name:
                out["file_name"] = a.file_name
            if hasattr(a, "w"):
                out["w"] = getattr(a, "w", None); out["h"] = getattr(a, "h", None)
            if hasattr(a, "duration"):
                out["duration"] = getattr(a, "duration", None)
    ph = getattr(med, "photo", None)
    if ph is not None:
        out["photo_id"] = getattr(ph, "id", None)
    return out


async def main(out_path="/data/e2e_target_snapshot.json"):
    settings = get_settings()
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    manager = SessionManager(settings=settings, redis=redis)
    async with async_session_factory() as db:
        from sqlalchemy import select
        acc = await db.scalar(select(TelegramSession).where(TelegramSession.id == B_SESSION_ID))
    client, release = await manager.acquire_client(acc)
    records = []
    try:
        peer = await client.get_entity(B_VIEW_PEER)
        msgs = await client.get_messages(peer, limit=400)
        for m in msgs:
            txt = m.message or ""
            if MARK not in txt and (m.media is None or m.id > 99999999):  # keep all media loosely
                if MARK not in txt:
                    continue
            fwd = getattr(m, "fwd_from", None)
            records.append({
                "target_id": m.id,
                "date": m.date.isoformat()[:19] if m.date else None,
                "fwd_date": fwd.date.isoformat()[:19] if fwd and fwd.date else None,
                "imported_fwd": bool(getattr(fwd, "imported", False)) if fwd else False,
                "text": (txt or "")[:60],
                "sender_id": getattr(getattr(m, "sender", None), "id", None),
                "media": _medsig(m),
                "grouped_id": getattr(m, "grouped_id", None),
                "reply_to": getattr(getattr(m, "reply_to", None), "reply_to_msg_id", None),
            })
        records.sort(key=lambda r: r["target_id"])
        (Path(out_path).write_text(json.dumps(records, ensure_ascii=False, indent=2)))
        for r in records:
            print(f"{r['target_id']} | {r['media'] and r['media']['ctor'] or 'NO-MEDIA'} | "
                  f"grouped={r['grouped_id']} reply={r['reply_to']} fwd_imported={r['imported_fwd']} | {r['text']!r}")
    finally:
        await release()


asyncio.run(main())