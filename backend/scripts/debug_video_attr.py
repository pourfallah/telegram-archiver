"""Build full InputMedia for video spec and find what telethon rejects."""
from __future__ import annotations
import asyncio
from pathlib import Path
from telethon.tl import functions, types
from app.services.telegram_imported_media import build_media_specs_from_archive

EXPORT_DIR = "/data/exports/_989394430100/David Rodriguez/run_15"


async def main():
    imptxt = (Path(EXPORT_DIR) / "import" / "import.txt").read_text(encoding="utf-8")
    specs = build_media_specs_from_archive(Path(EXPORT_DIR), imptxt, None)
    for s in specs:
        if s.filename != "video_1029540.mp4":
            continue
        print("spec:", s.media_type, s.mime_type, s.extra)
        # simulate the worker's path: build attributes then the InputMediaUploadedDocument
        from app.services.telegram_imported_media import TelegramImportedMediaService
        svc = TelegramImportedMediaService(None)
        attrs = svc._build_document_attributes(s)
        attrs.append(types.DocumentAttributeFilename(file_name=s.filename))
        media = types.InputMediaUploadedDocument(
            file=None, mime_type=s.mime_type, attributes=attrs)
        print("media:", media)
        # try building the full request (client-side serialization)
        try:
            req = functions.messages.UploadImportedMediaRequest(
                peer=types.InputPeerUser(user_id=1, access_hash=2),
                import_id=1, file_name=s.filename, media=media)
            print("request OK:", type(req).__name__)
        except Exception as e:
            print("REQ ERR:", repr(e))
    # also try video sample attributes
    print("attrs debug:")
    for a in attrs:
        print("  ", a)


asyncio.run(main())