"""Full real upload for video spec (real client, fake import_id → reproduce)."""
from __future__ import annotations
import asyncio
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
        svc = TelegramImportedMediaService(client)
        for s in specs:
            if s.filename not in ("video_1029540.mp4", "animation_29055.mp4"):
                continue
            try:
                r = await svc.upload_imported_media(
                    await client.get_input_entity(B_VIEW_PEER), 1234567890123456789, s)
                print(f"{s.filename}: {r.returned_ctor} err={r.error}")
            except Exception as e:
                print(f"{s.filename}: EXC {type(e).__name__}: {e}")
    finally:
        await release()


asyncio.run(main())