"""READ-ONLY: list all A dialogs + media presence, focus E2E Test Chat."""
from __future__ import annotations
import asyncio
import redis.asyncio as aioredis
from app.config import get_settings
from app.database import async_session_factory
from app.models import TelegramSession
from app.services.session_manager import SessionManager


def _medsig(m):
    med = m.media
    if med is None:
        return "NONE"
    ctor = type(med).__name__
    doc = getattr(med, "document", None)
    if doc is not None:
        attrs = [type(a).__name__ for a in (getattr(doc, "attributes", None) or [])]
        return f"{ctor} attrs={attrs}"
    photo = getattr(med, "photo", None)
    return f"{ctor} photo={bool(photo)}"


async def main():
    settings = get_settings()
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    manager = SessionManager(settings=settings, redis=redis)
    async with async_session_factory() as db:
        from sqlalchemy import select
        acc_a = await db.scalar(select(TelegramSession).where(TelegramSession.id == 1))
    client, release = await manager.acquire_client(acc_a)
    try:
        me = await client.get_me()
        print("A:", me.id, me.first_name)
        for d in await client.get_dialogs(limit=200):
            ent = d.entity
            title = getattr(ent, "title", None) or getattr(ent, "first_name", None)
            utype = type(ent).__name__
            print(f"dialog id={d.id} type={utype} title={title!r} unread={d.unread_count}")
            if title and ("E2E" in str(title).lower() or "test chat" in str(title).lower()):
                msgs = await client.get_messages(d.entity, limit=60)
                print(f"  --- {len(msgs)} msgs (newest-first) ---")
                for x in reversed(msgs):
                    print("   ", x.id, "|", _medsig(x).ljust(46), "|", repr((x.message or "")[:40]))
    finally:
        await release()


asyncio.run(main())