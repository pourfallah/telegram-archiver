"""Variant E: WhatsApp-style filename + delay before start.

Real WhatsApp export filenames look like:
    00000042-PHOTO-2023-05-01-12:00:00.jpg
Maybe the server matches by extension+pattern, or the import needs time to
process uploads before StartHistoryImport. This variant:
  - names the file like a real WA export
  - waits 5s after uploadImportedMedia before startHistoryImport
  - also tries marker with the full WA-style name

Run: docker exec telegram-archiver-worker-1 python -m app.services.media_marker_test3
"""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

MEDIA = Path("/data/exports/_989394430100/David Rodriguez/archive/media/photo/photo_0.jpg")
WA_NAME = "00000001-PHOTO-2024-07-07-08:00:00.jpg"


async def main() -> None:
    import redis.asyncio as aioredis
    from sqlalchemy import select
    from telethon import types
    from telethon.tl.functions import messages as tl

    from app.config import get_settings
    from app.database import async_session_factory
    from app.models import TelegramSession
    from app.services.session_manager import SessionManager

    settings = get_settings()
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    manager = SessionManager(settings=settings, redis=redis)
    async with async_session_factory() as db:
        acc = await db.scalar(select(TelegramSession).where(TelegramSession.id == 3))

    client, release = await manager.acquire_client(acc)
    try:
        peer = await client.get_input_entity("pourfallah")
        content = (
            "7/7/2024, 8:00 AM - Tester3: variantE text\n"
            f"7/7/2024, 8:00 AM - Tester3: <attached: {WA_NAME}>\n"
            "7/7/2024, 8:01 AM - Tester3: variantE end\n"
        )
        p = Path("/data/exports/experiments/mm3/variantE.txt")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

        await client(tl.CheckHistoryImportRequest(import_head=content))
        f = await client.upload_file(p)
        init = await client(tl.InitHistoryImportRequest(peer=peer, file=f, media_count=1))

        tmp = Path("/tmp") / WA_NAME
        shutil.copy(MEDIA, tmp)
        handle = await client.upload_file(tmp, file_name=WA_NAME)
        media = types.InputMediaUploadedPhoto(file=handle)
        await client(tl.UploadImportedMediaRequest(
            peer=peer, import_id=init.id, file_name=WA_NAME, media=media))

        await asyncio.sleep(8)  # give the server time to register the token
        await client(tl.StartHistoryImportRequest(peer=peer, import_id=init.id))
        print("started")

        for i in range(5):
            await asyncio.sleep(25)
            hits = await client.get_messages(peer, search="variantE", limit=5)
            for m in sorted(hits, key=lambda x: x.id):
                fwd = getattr(m, "fwd_from", None)
                nxt = await client.get_messages(peer, ids=[m.id + 1])
                n = nxt[0] if nxt and nxt[0] else None
                print(m.id, m.date.isoformat()[:16],
                      "imp" if getattr(fwd, "imported", False) else "   ",
                      repr((m.message or "")[:30]),
                      "| next:", repr((n.message or "")[:40]) if n else "-",
                      type(n.media).__name__ if n and n.media else "")
    finally:
        await release()


if __name__ == "__main__":
    asyncio.run(main())
