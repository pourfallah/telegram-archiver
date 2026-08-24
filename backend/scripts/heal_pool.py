"""Diagnose + heal zombie Telegram clients in the API session pool.

Usage: docker exec telegram-archiver-backend-1 python /tmp/heal_pool.py [account_id]
"""
import asyncio
import sys


async def main() -> None:
    import redis.asyncio as aioredis
    from sqlalchemy import select

    from app.config import get_settings
    from app.database import async_session_factory
    from app.models import TelegramSession
    from app.services.session_manager import SessionManager

    account_id = int(sys.argv[1]) if len(sys.argv) > 1 else None

    settings = get_settings()
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    sm = SessionManager(settings=settings, redis=redis)

    async with async_session_factory() as db:
        if account_id:
            accs = [
                await db.scalar(
                    select(TelegramSession).where(TelegramSession.id == account_id)
                )
            ]
        else:
            accs = (await db.scalars(select(TelegramSession))).all()

    for acc in accs:
        if not acc or not acc.session_encrypted:
            print(f"acc{acc.id}: no session — skip")
            continue
        try:
            client, release = await asyncio.wait_for(
                sm.acquire_client(acc), timeout=20
            )
        except asyncio.TimeoutError:
            print(f"acc{acc.id}: acquire TIMEOUT (lock wedged) — drop")
            await sm.drop(acc.id)
            continue
        try:
            try:
                me = await asyncio.wait_for(client.get_me(), timeout=10)
                print(f"acc{acc.id}: healthy ({getattr(me, 'phone', '?')})")
            except asyncio.TimeoutError:
                print(f"acc{acc.id}: ZOMBIED — dropping from pool")
                await sm.drop(acc.id)
                print(f"acc{acc.id}: dropped")
        finally:
            await release()

    print("done")


asyncio.run(main())
