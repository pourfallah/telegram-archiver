"""Reproduce the media upload TLObject error for the exact spec."""
from __future__ import annotations
import asyncio, json
from pathlib import Path
import redis.asyncio as aioredis
from app.config import get_settings
from app.database import async_session_factory
from app.models import TelegramSession
from app.services.session_manager import SessionManager
from app.services.telegram_imported_media import TelegramImportedMediaService, build_media_specs_from_archive

B_SESSION_ID = 3
B_VIEW_PEER = 165649921
EXPORT_DIR = "/data/exports/_989394430100/David Rodriguez/run_15"


async def main():
    settings = get_settings()
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    manager = SessionManager(settings=settings, redis=redis)
    async with async_session_factory() as db:
        from sqlalchemy import select
        acc = await db.scalar(select(TelegramSession).where(TelegramSession.id == B_SESSION_ID))
    client, release = await manager.acquire_client(acc)
    try:
        imptxt = (Path(EXPORT_DIR) / "import" / "import.txt").read_text(encoding="utf-8")
        specs = build_media_specs_from_archive(Path(EXPORT_DIR), imptxt, None)
        print("total specs:", len(specs))
        for s in specs:
            print(f"  src={s.source_message_id} fname={s.filename} type={s.media_type} mime={s.mime_type} path_exists={Path(s.file_path).exists()}")
        # try building input media for the document .bin spec
        svc = TelegramImportedMediaService(client)
        for s in specs:
            if s.filename == "document_326.bin":
                try:
                    im = svc.build_input_media(s)
                    print("built input media for document_326.bin:", type(im).__name__)
                except Exception as e:
                    print("ERROR building input media document_326.bin:", repr(e))
                # try direct upload to telegram to see error
                try:
                    peer = await client.get_input_entity(B_VIEW_PEER)
                    res = await svc.upload_imported_media(peer, 1234567890123456789, s)  # will fail on bad import_id but shows earlier errors
                    print("upload result:", res)
                except Exception as e:
                    print("upload attempt error:", repr(e))
    finally:
        await release()


asyncio.run(main())