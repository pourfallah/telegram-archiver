"""Read the actual target messages of the user's import (job 54 / export 18) via MTProto."""
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
        ms = await c.get_messages(p, limit=30)
        for m in sorted(ms, key=lambda x: x.id)[-15:]:
            t = (m.message or "")[:50]
            md = m.media
            if isinstance(md, types.MessageMediaDocument):
                doc = md.document
                attrs = [type(a).__name__ for a in doc.attributes]
                print(f"{m.id} DOC mime={doc.mime_type} attrs={attrs} | text={t!r}")
            elif isinstance(md, types.MessageMediaPhoto):
                print(f"{m.id} PHOTO | text={t!r}")
            else:
                print(f"{m.id} {type(md).__name__ if md else 'None'} | text={t!r}")
    finally:
        await r()

asyncio.run(main())