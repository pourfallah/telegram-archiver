"""One-shot B-side clear (drain to empty). A untouched. B session only."""
from __future__ import annotations
import asyncio
import redis.asyncio as aioredis
from app.config import get_settings
from app.database import async_session_factory
from app.models import TelegramSession
from app.services.session_manager import SessionManager

B_SESSION_ID = 3
B_VIEW_PEER = 165649921
A_SESSION_ID = 1
A_VIEW_PEER = 7768075024

async def count(manager, acc, peer_id, label):
    c, r = await manager.acquire_client(acc)
    try:
        p = await c.get_entity(peer_id)
        ms = await c.get_messages(p, limit=500)
        print(f"  [{label}] total={len(ms)}")
        return len(ms)
    finally:
        await r()

async def main():
    settings = get_settings()
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    manager = SessionManager(settings=settings, redis=redis)
    async with async_session_factory() as db:
        from sqlalchemy import select
        acc_a = await db.scalar(select(TelegramSession).where(TelegramSession.id == A_SESSION_ID))
        acc_b = await db.scalar(select(TelegramSession).where(TelegramSession.id == B_SESSION_ID))
    from telethon import functions
    print("=== BEFORE ===")
    n_a = await count(manager, acc_a, A_VIEW_PEER, "A")
    n_b = await count(manager, acc_b, B_VIEW_PEER, "B")
    cb, rb = await manager.acquire_client(acc_b)
    try:
        p = await cb.get_entity(B_VIEW_PEER)
        for i in range(1, 12):
            if n_b == 0:
                break
            await cb(functions.messages.DeleteHistoryRequest(
                peer=p, max_id=0, just_clear=True, revoke=False))
            await asyncio.sleep(6)
            n_b = await count(manager, acc_b, B_VIEW_PEER, f"B iter{i}")
    finally:
        await rb()
    print("=== AFTER ===")
    n_a2 = await count(manager, acc_a, A_VIEW_PEER, "A")
    n_b2 = await count(manager, acc_b, B_VIEW_PEER, "B")
    print(f"A intact: {n_a2 == n_a} (was {n_a}, now {n_a2})")
    print(f"B empty: {n_b2 == 0} (now {n_b2})")

asyncio.run(main())