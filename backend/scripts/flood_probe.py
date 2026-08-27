import asyncio
from telethon import functions
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
        acc_a = await db.scalar(select(TelegramSession).where(TelegramSession.id == 1))
    cb, rb = await manager.acquire_client(acc_b)
    ca, ra = await manager.acquire_client(acc_a)
    try:
        peer_a = await ca.get_entity(7768075024)
        body = b'[01/01/2023, 10:00:00] - Alice: flood_probe\n'
        try:
            fh2 = await ca.upload_file(body)
            r = await ca(functions.messages.InitHistoryImportRequest(
                peer=peer_a, file=fh2, media_count=0))
            print('A-side init OK', getattr(r, 'id'))
        except Exception as e:
            print('A init:', type(e).__name__, str(e)[:60])
    finally:
        await rb()
        await ra()


asyncio.run(t())
