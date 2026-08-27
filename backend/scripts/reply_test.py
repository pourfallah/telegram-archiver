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
        acc_a = await db.scalar(select(TelegramSession).where(TelegramSession.id == 1))
    cb, rb = await manager.acquire_client(acc_b)
    ca, ra = await manager.acquire_client(acc_a)
    try:
        peer_b = await cb.get_entity(165649921)
        before = {m.id for m in await cb.get_messages(peer_b, limit=30)}
        body = ('[01/01/2024, 10:00:00] - Alice: REPLY_PARENT\n'
                '[01/01/2024, 10:01:00] - Alice: REPLY_CHILD\n')
        fh = await cb.upload_file(body.encode())
        init = await cb(functions.messages.InitHistoryImportRequest(peer=peer_b, file=fh, media_count=0))
        await cb(functions.messages.StartHistoryImportRequest(peer=peer_b, import_id=getattr(init,'id')))
        await asyncio.sleep(20)
        msgs = [m for m in await cb.get_messages(peer_b, limit=6) if m.id not in before]
        for m in sorted(msgs, key=lambda x: x.id):
            rt = getattr(m, 'reply_to', None)
            rid = getattr(rt, 'reply_to_msg_id', None) if rt else None
            print(m.id, '|', repr((m.message or '')[:30]), '| reply_to=', rid)
    finally:
        await rb()
        await ra()

asyncio.run(t())