"""Variant C/D test: marker syntax + media_count semantics.

Variants:
  C: WhatsApp ts, marker '<attached:photo_0.jpg>' (no space)
  D: WhatsApp ts, marker '<attached: photo_0.jpg>' but media_count=1 declared
     BEFORE uploading (strict order: init -> upload -> start) and file uploaded
     as InputMediaUploadedDocument with image/jpeg mime (not Photo).

Run: docker exec telegram-archiver-worker-1 python -m app.services.media_marker_test2
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path

MEDIA = Path("/data/exports/_989394430100/David Rodriguez/archive/media/photo/photo_0.jpg")
STICKER = Path("/data/exports/_989394430100/David Rodriguez/archive/media/sticker/sticker_160416.webm")


def _wa(dt: datetime) -> str:
    return f"{dt.month}/{dt.day}/{dt.year}, {dt.strftime('%I:%M %p').lstrip('0')}"


async def run(client, peer, name, content, media_count, uploads):
    from telethon import types
    from telethon.tl.functions import messages as tl

    p = Path(f"/data/exports/experiments/mm2/{name}.txt")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")

    await client(tl.CheckHistoryImportRequest(import_head=content[:4000]))
    f = await client.upload_file(p)
    init = await client(tl.InitHistoryImportRequest(peer=peer, file=f, media_count=media_count))

    for fname, builder in uploads:
        handle = await client.upload_file(builder["path"], file_name=fname)
        if builder["kind"] == "doc":
            media = types.InputMediaUploadedDocument(
                file=handle,
                mime_type=builder["mime"],
                attributes=[types.DocumentAttributeFilename(file_name=fname)],
            )
        else:
            media = types.InputMediaUploadedPhoto(file=handle)
        await client(tl.UploadImportedMediaRequest(
            peer=peer, import_id=init.id, file_name=fname, media=media))
    await client(tl.StartHistoryImportRequest(peer=peer, import_id=init.id))
    return {"variant": name, "started": True}


async def main():
    import redis.asyncio as aioredis

    from app.config import get_settings
    from app.database import async_session_factory
    from app.models import TelegramSession
    from app.services.session_manager import SessionManager

    settings = get_settings()
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    manager = SessionManager(settings=settings, redis=redis)
    async with async_session_factory() as db:
        from sqlalchemy import select

        acc = await db.scalar(select(TelegramSession).where(TelegramSession.id == 3))
    client, release = await manager.acquire_client(acc)
    try:
        peer = await client.get_input_entity("pourfallah")
        d1 = "7/7/2024, 8:00 AM"
        d2 = "7/7/2024, 8:01 AM"

        # Variant C: no-space marker, photo as document w/ mime
        c_content = (
            f"{d1} - Tester2: variantC text\n"
            f"{d1} - Tester2: <attached:photo_0.jpg>\n"
            f"{d2} - Tester2: variantC end\n"
        )
        r1 = await run(client, peer, "variantC", c_content, 1,
                       [("photo_0.jpg", {"path": MEDIA, "kind": "doc", "mime": "image/jpeg"})])

        # Variant D: sticker as doc with proper webm mime + emoji attr
        d_content = (
            f"{d1} - Tester2: variantD text\n"
            f"{d2} - Tester2: <attached: sticker_160416.webm>\n"
        )
        r2 = await run(client, peer, "variantD", d_content, 1,
                       [("sticker_160416.webm", {"path": STICKER, "kind": "doc", "mime": "video/webm"})])
        print(json.dumps([r1, r2]))

        for i in range(6):
            await asyncio.sleep(25)
            hits_c = await client.get_messages(peer, search="variantC", limit=5)
            hits_d = await client.get_messages(peer, search="variantD", limit=5)
            print(f"--- poll {i+1}")
            for m in sorted(list(hits_c) + list(hits_d), key=lambda x: x.id):
                fwd = getattr(m, "fwd_from", None)
                print(m.id, m.date.isoformat()[:16],
                      "imp" if getattr(fwd, "imported", False) else "   ",
                      repr((m.message or "")[:35]),
                      type(m.media).__name__ if m.media else "")
    finally:
        await release()


if __name__ == "__main__":
    asyncio.run(main())
