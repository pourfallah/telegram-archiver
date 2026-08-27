import asyncio
from telethon import functions, types
from app.database import async_session_factory
from app.models import TelegramSession
from app.services.session_manager import SessionManager
from app.config import get_settings
import redis.asyncio as aioredis


async def t():
    settings = get_settings()
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    manager = SessionManager(settings=settings, redis=redis)
    async with async_session_factory() as db:
        from sqlalchemy import select
        acc_b = await db.scalar(select(TelegramSession).where(TelegramSession.id == 3))
    cb, rb = await manager.acquire_client(acc_b)
    try:
        peer_b = await cb.get_entity(165649921)
        res = await cb(functions.messages.GetMessagesRequest(
            id=[types.InputMessageID(id=i) for i in (1696, 1697)]))
        for m in res.messages:
            if isinstance(m, types.Message):
                print(m.id, '|', m.date.isoformat()[:19], '|',
                      repr((m.message or '')[:36]), '| media:',
                      type(m.media).__name__ if m.media else None)
            else:
                print('EMPTY:', type(m).__name__)
    finally:
        await rb()


asyncio.run(t())
