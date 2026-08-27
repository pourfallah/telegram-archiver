"""Inspect video/audio/sticker spec attributes from the fixed service."""
from __future__ import annotations
import asyncio
from pathlib import Path
from app.services.telegram_imported_media import TelegramImportedMediaService, build_media_specs_from_archive

EXPORT_DIR = "/data/exports/_989394430100/David Rodriguez/run_15"


async def main():
    imptxt = (Path(EXPORT_DIR) / "import" / "import.txt").read_text(encoding="utf-8")
    specs = build_media_specs_from_archive(Path(EXPORT_DIR), imptxt, None)
    for s in specs:
        print(f"{s.filename}: type={s.media_type} mime={s.mime_type} extra={s.extra}")
        svc = TelegramImportedMediaService(None)
        try:
            attrs = svc._build_document_attributes(s)
            print("   attrs:", [type(a).__name__ for a in attrs])
        except Exception as e:
            print("   attr-build ERR:", repr(e))


asyncio.run(main())