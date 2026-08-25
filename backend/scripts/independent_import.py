"""Independent minimal 1-photo import test — EXACT real-WhatsApp export syntax.

Real WhatsApp exports (as accepted by Telegram's importer) look like:
    [26/05/2018, 15:12:30] John Doe: <attached: 00000042-PHOTO-2018-05-26-15-12-30.jpg>
with BRACKET-delimited timestamps INCLUDING SECONDS, DD/MM/YYYY order.

This standalone script (kept separate from the app's serializer) builds that
exact form and runs the full import cycle with raw MTProto via Telethon.

Usage: docker cp backend/scripts/independent_import.py worker:/app/ && \
       docker exec worker python /app/independent_import.py
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

OUT = Path("/data/exports/experiments/independent")


def _wa(dt: datetime) -> str:
    # WhatsApp: [DD/MM/YYYY, HH:MM:SS] with seconds, 24-hour
    return dt.strftime("[%d/%m/%Y, %H:%M:%S]")


async def run(client, peer_identifier: str, chat_title: str) -> dict:
    from telethon import types
    from telethon.tl.functions import messages as tl

    result: dict = {}
    OUT.mkdir(parents=True, exist_ok=True)

    # --- message_file: 3 messages; the middle one references a photo ---
    d1 = datetime(2024, 1, 5, 10, 0, 0, tzinfo=UTC)
    d2 = datetime(2024, 1, 5, 10, 0, 30, tzinfo=UTC)
    d3 = datetime(2024, 1, 5, 10, 1, 0, tzinfo=UTC)

    # Photo file with a real WhatsApp-style filename
    src_photo = Path("/data/exports/_989394430100/David Rodriguez/archive/media/photo/photo_0.jpg")
    wa_name = "00000042-PHOTO-2024-01-05-10-00-30.jpg"
    photo_path = OUT / wa_name
    shutil.copy(src_photo, photo_path)

    lines = [
        f"{_wa(d1)} John Doe: Hello",
        f"{_wa(d2)} Jane Smith: <attached: {wa_name}>",
        f"{_wa(d3)} John Doe: After photo",
    ]
    import_file = OUT / "import.txt"
    import_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    sha = hashlib.sha256(import_file.read_bytes()).hexdigest()
    result["import_file"] = str(import_file)
    result["import_file_content"] = "\n".join(lines)
    result["import_file_sha256"] = sha
    result["import_file_size"] = import_file.stat().st_size

    peer = await client.get_input_entity(peer_identifier)

    # 1. checkHistoryImport (first 100 lines)
    head = "\n".join(lines[:100])
    parsed = await client(tl.CheckHistoryImportRequest(import_head=head))
    result["checkHistoryImport"] = {
        "pm": bool(getattr(parsed, "pm", False)),
        "title": getattr(parsed, "title", None),
    }

    # 2. initHistoryImport (media_count=1)
    input_file = await client.upload_file(import_file, file_name="import.txt")
    init = await client(tl.InitHistoryImportRequest(
        peer=peer, file=input_file, media_count=1))
    import_id = int(getattr(init, "id", 0))
    result["initHistoryImport"] = {"import_id": import_id, "request_media_count": 1}

    # 3. uploadImportedMedia — the single photo, EXACT WA filename
    photo_handle = await client.upload_file(photo_path, file_name=wa_name)
    photo_media = types.InputMediaUploadedPhoto(file=photo_handle)
    result["uploadImportedMedia"] = {
        "file_name": wa_name,
        "photo_input_media": str(type(photo_media).__name__),
    }
    try:
        up = await client(tl.UploadImportedMediaRequest(
            peer=peer, import_id=import_id, file_name=wa_name, media=photo_media))
        result["uploadImportedMedia"]["result_type"] = type(up).__name__
        result["uploadImportedMedia"]["result_preview"] = str(up)[:300]
        result["uploadImportedMedia"]["ok"] = True
    except Exception as exc:  # noqa: BLE001
        result["uploadImportedMedia"]["error"] = f"{type(exc).__name__}: {exc}"
        result["uploadImportedMedia"]["ok"] = False

    # 4. startHistoryImport
    started = await client(tl.StartHistoryImportRequest(peer=peer, import_id=import_id))
    result["startHistoryImport"] = bool(started)

    # rpc-log
    rpc_log = OUT / "rpc-log.json"
    rpc_log.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return result


async def main() -> None:
    import redis.asyncio as aioredis
    from sqlalchemy import select

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
        result = await run(client, "pourfallah", "First Dev.")
        print(json.dumps(result, indent=2, default=str))

        # Wait for materialization, then read target
        for i in range(6):
            await asyncio.sleep(25)
            hits = await client.get_messages("pourfallah", search="After photo", limit=5)
            if hits:
                m = hits[0]
                # read the block: the photo message is the one before "After photo"
                prev = await client.get_messages("pourfallah", ids=[m.id - 1])
                p = prev[0] if prev and prev[0] else None
                print(f"--- mode poll {i + 1}: After-photo id={m.id} photo-border id={p.id if p else '?'}")
                for probe in ([m.id - 1, m.id] if p else [m.id]):
                    mm = await client.get_messages("pourfallah", ids=[probe])
                    mm = mm[0] if mm else None
                    if not mm:
                        continue
                    fwd = getattr(mm, "fwd_from", None)
                    print("   id", mm.id, "|", mm.date.isoformat()[:16],
                          "| imported:", getattr(fwd, "imported", None) if fwd else None,
                          "| text:", repr((mm.message or "")[:35]),
                          "| media:", type(mm.media).__name__ if mm.media else "None")
    finally:
        await release()


if __name__ == "__main__":
    asyncio.run(main())
