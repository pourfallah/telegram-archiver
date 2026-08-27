"""READ-ONLY live inspection of source chats from Account A.

Determines the TRUE media constructors of source messages — i.e. whether real
photos/stickers/albums exist and, if the canonical archive says 'document',
whether the EXPORT (classify_media) dropped the type.

Safe: only get_messages / get_me. No writes, no clears, no sends.
"""
from __future__ import annotations

import asyncio

import redis.asyncio as aioredis

from app.config import get_settings
from app.database import async_session_factory
from app.models import TelegramSession
from app.services.session_manager import SessionManager

A_SESSION_ID = 1   # +989..0100 First Dev.
A_VIEW_DAVID = 7768075024   # David as seen from A
# E2E Test Chat peer id (as seen from A): resolve dynamically below.


def _medsig(m):
    med = m.media
    if med is None:
        return "NONE"
    ctor = type(med).__name__
    doc = getattr(med, "document", None)
    if doc is not None:
        attrs = [type(a).__name__ for a in (getattr(doc, "attributes", None) or [])]
        return f"{ctor} mime={getattr(doc,'mime_type',None)} attrs={attrs}"
    photo = getattr(med, "photo", None)
    if photo is not None:
        return f"{ctor} photo_id={getattr(photo,'id',None)}"
    return ctor


async def main():
    settings = get_settings()
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    manager = SessionManager(settings=settings, redis=redis)
    async with async_session_factory() as db:
        from sqlalchemy import select
        acc_a = await db.scalar(select(TelegramSession).where(TelegramSession.id == A_SESSION_ID))
    client, release = await manager.acquire_client(acc_a)
    try:
        me = await client.get_me()
        print("A me id:", me.id, me.first_name)
        # History of the A<->B private chat (David).
        peer = await client.get_entity(A_VIEW_DAVID)
        msgs = await client.get_messages(peer, limit=40)
        print(f"\n=== A<->David chat history ({len(msgs)} recent, newest-first) ===")
        for m in reversed(msgs):
            print(m.id, "|", _medsig(m).ljust(70), "|", repr((m.message or "")[:38]))
        # Dialogs: find 'E2E Test Chat'
        print("\n=== dialogs ===")
        dialogs = await client.get_dialogs(limit=100)
        for d in dialogs:
            title = getattr(d.entity, "title", None) or getattr(d.entity, "first_name", None)
            if title and ("E2E" in title.lower() or "chat" in title.lower()):
                print("dialog:", d.id, type(d.entity).__name__, repr(title), "msg_pts", d.unread_count)
                try:
                    em = await client.get_messages(d.entity, limit=50)
                    print(f"  {len(em)} recent messages (newest-first):")
                    for x in reversed(em):
                        print("   ", x.id, "|", _medsig(x).ljust(70), "|", repr((x.message or "")[:38]))
                except Exception as ex:
                    print("  read err", ex)
    finally:
        await release()


asyncio.run(main())