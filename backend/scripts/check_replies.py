"""Check reply structure on imported target messages (reply parent/child)."""
from __future__ import annotations
import asyncio
import redis.asyncio as aioredis
from app.config import get_settings
from app.database import async_session_factory
from app.models import TelegramSession
from app.services.session_manager import SessionManager
from sqlalchemy import select

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
            t = (m.message or "")
            if "REPLY" in t or "REACTION" in t:
                rt = m.reply_to
                if rt:
                    rtm = rt.reply_to_msg_id if hasattr(rt, "reply_to_msg_id") else getattr(rt, "reply_to_msg_id", None)
                    top = getattr(rt, "top_msg_id", None)
                    # try to fetch the replied message
                    replied = None
                    if rtm:
                        try:
                            replied = await c.get_messages(p, ids=rtm)
                            replied_text = (replied.message or "")[:40] if replied else None
                        except Exception as e:
                            replied_text = f"ERR:{e}"
                    print(f"{m.id} | reply_to_msg_id={rtm} top={top} replied={replied_text!r} | {t[:45]!r}")
                else:
                    print(f"{m.id} | NO reply_to | {t[:45]!r}")
    finally:
        await r()

asyncio.run(main())