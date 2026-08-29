"""Detail dump of imported media attributes (one-shot job 53 verification)."""
from __future__ import annotations
import asyncio
import redis.asyncio as aioredis
from app.config import get_settings
from app.database import async_session_factory
from app.models import TelegramSession
from app.services.session_manager import SessionManager
from sqlalchemy import select
from telethon.tl import types

settings = get_settings()
redis = aioredis.from_url(settings.redis_url, decode_responses=True)
manager = SessionManager(settings=settings, redis=redis)

async def main():
    async with async_session_factory() as db:
        acc = await db.scalar(select(TelegramSession).where(TelegramSession.id == 3))
    c, r = await manager.acquire_client(acc)
    try:
        p = await c.get_entity(165649921)
        ms = await c.get_messages(p, limit=80)
        for m in sorted(ms, key=lambda x: x.id):
            if m.id < 5094:
                continue
            md = m.media
            if isinstance(md, types.MessageMediaDocument):
                attrs = []
                for a in md.document.attributes:
                    nm = type(a).__name__
                    if "Video" in nm:
                        attrs.append("Video(dur=%s,w=%s)" % (getattr(a, "duration", "?"), getattr(a, "w", "?")))
                    elif "Animated" in nm:
                        attrs.append("Animated")
                    elif "Audio" in nm:
                        attrs.append("Audio(title=%r)" % (getattr(a, "title", "?"),))
                    elif "Sticker" in nm:
                        attrs.append("Sticker(alt=%r)" % (getattr(a, "alt", "?"),))
                    elif "Filename" in nm:
                        attrs.append("Filename(%s)" % (getattr(a, "file_name", "?"),))
                    else:
                        attrs.append(nm)
                mime = getattr(md.document, "mime_type", "?")
                print("%s DOC mime=%s attrs=%s" % (m.id, mime, attrs))
            elif isinstance(md, types.MessageMediaPhoto):
                gid = getattr(m, "grouped_id", None)
                print("%s PHOTO grouped_id=%s" % (m.id, gid))
            else:
                pass
    finally:
        await r()

asyncio.run(main())