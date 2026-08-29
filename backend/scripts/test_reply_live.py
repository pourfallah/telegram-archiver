"""REPLY TEST: send a real reply from B to the imported REPLY_PARENT, read back.

Proves reply reconstruction works on the imported target (post-import phase).
"""
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

TARGET_PARENT_TEXT = "RECOVERY_FINAL_20260827_REPLY_PARENT_010"

async def main():
    async with async_session_factory() as db:
        acc = await db.scalar(select(TelegramSession).where(TelegramSession.id == 3))
    c, r = await manager.acquire_client(acc)
    try:
        p = await c.get_entity(165649921)
        # find the imported parent
        ms = await c.get_messages(p, limit=80)
        parent = None
        for m in ms:
            if (m.message or "").strip() == TARGET_PARENT_TEXT:
                parent = m
                break
        if parent is None:
            print("PARENT NOT FOUND")
            return
        print(f"parent id={parent.id} text={parent.message!r}")

        # send a real reply to it
        sent = await c.send_message(p, "REPLY_RECOVERY_TEST_OK", reply_to=parent.id)
        print(f"sent reply id={sent.id}")

        # read it back fresh
        got = await c.get_messages(p, ids=sent.id)
        rt = got.reply_to
        print(f"reply message id={got.id}")
        print(f"  reply_to object: {type(rt).__name__ if rt else None}")
        if rt:
            rtm = getattr(rt, "reply_to_msg_id", None)
            print(f"  reply_to_msg_id: {rtm} (parent={parent.id}) -> MATCH: {rtm == parent.id}")
            # fetch what it points at
            target = await c.get_messages(p, ids=rtm)
            print(f"  points at: id={target.id} text={target.message!r}")
        # also verify via raw message
        raw = await c.get_messages(p, ids=sent.id)
        print(f"  raw reply_to_msg_id attr: {getattr(raw.reply_to, 'reply_to_msg_id', None) if raw.reply_to else None}")
    finally:
        await r()

asyncio.run(main())