"""Independent media-type matrix test: photo+caption, document, video, sticker.

Uses the CORRECT real-WA syntax that ATTACHES media:
    [DD/MM/YYYY, HH:MM:SS] Name: message
    [DD/MM/YYYY, HH:MM:SS] Name: <attached: 000000NN-TYPE-....ext>

Each import is a fresh 3-message block. Caption test puts the media marker and
caption as one logical media message.

Run: docker cp backend/scripts/media_matrix.py worker:/app/ && docker exec worker python /app/media_matrix.py
"""
from __future__ import annotations

import asyncio
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

OUT = Path("/data/exports/experiments/matrix")


def _wa(dt: datetime) -> str:
    return dt.strftime("[%d/%m/%Y, %H:%M:%S]")


async def run_one(client, peer, block_id: str, lines: list[str], files: list[tuple[str, Path, str]]) -> dict:
    """lines: already-formatted import lines. files: (wa_name, local_path, media_kind)."""
    from telethon import types
    from telethon.tl.functions import messages as tl

    OUT.mkdir(parents=True, exist_ok=True)
    import_file = OUT / f"block_{block_id}.txt"
    import_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    r: dict = {"block": block_id, "lines": lines}

    try:
        await client(tl.CheckHistoryImportRequest(import_head="\n".join(lines[:100])))
    except Exception as exc:  # noqa: BLE001
        r["check_error"] = f"{type(exc).__name__}: {exc}"
        return r
    input_file = await client.upload_file(import_file, file_name=f"block_{block_id}.txt")
    init = await client(tl.InitHistoryImportRequest(peer=peer, file=input_file, media_count=len(files)))
    import_id = int(getattr(init, "id", 0))
    r["import_id"] = import_id

    try:
        for wa_name, local_path, kind in files:
            tmp = OUT / f"{block_id}_{wa_name}"
            try:
                shutil.copy(local_path, tmp)
            except shutil.SameFileError:
                tmp = local_path
            handle = await client.upload_file(tmp, file_name=wa_name)
            if kind == "photo":
                media = types.InputMediaUploadedPhoto(file=handle)
            else:
                mime = {"document": "application/pdf", "video": "video/mp4", "sticker": "video/webm"}[kind]
                attrs = [types.DocumentAttributeFilename(file_name=wa_name)]
                media = types.InputMediaUploadedDocument(file=handle, mime_type=mime, attributes=attrs)
            up = await client(tl.UploadImportedMediaRequest(peer=peer, import_id=import_id, file_name=wa_name, media=media))
            r[f"upload_{wa_name}"] = {"kind": kind, "result": type(up).__name__}

        started = await client(tl.StartHistoryImportRequest(peer=peer, import_id=import_id))
        r["started"] = bool(started)
    except Exception as exc:  # noqa: BLE001
        r["error"] = f"{type(exc).__name__}: {exc}"
    return r


def wa_name(idx: int, tag: str, d: datetime) -> str:
    return f"000000{idx:02d}-{tag}-{d.strftime('%Y-%m-%d-%H-%M-%S')}{'.pdf' if tag=='DOC' else '.mp4' if tag=='VID' else '.webm' if tag=='STK' else '.jpg'}"


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
        peer = await client.get_input_entity("pourfallah")
        D = Path("/data/exports/_989394430100/David Rodriguez/archive/media")
        results = []

        # Each block is one media type, unique dates to separate blocks
        bases = {
            "photo_cap": datetime(2024, 2, 1, 9, 0, 0, tzinfo=UTC),
            "document": datetime(2024, 3, 1, 9, 0, 0, tzinfo=UTC),
            "video": datetime(2024, 4, 1, 9, 0, 0, tzinfo=UTC),
            "sticker": datetime(2024, 5, 1, 9, 0, 0, tzinfo=UTC),
        }

        # --- PHOTO + CAPTION ---
        b = bases["photo_cap"]
        pn = wa_name(10, "PHOTO", b)
        r = await run_one(client, peer, "photocap", [
            f"{_wa(b)} John Doe: Before cap",
            f"{_wa(b + __import__('datetime').timedelta(seconds=30))} Jane Smith: <attached: {pn}>",
            f"{_wa(b + __import__('datetime').timedelta(seconds=40))} Jane Smith: This is the photo",
            f"{_wa(b + __import__('datetime').timedelta(seconds=50))} John Doe: After cap",
        ], [(pn, D / "photo" / "photo_0.jpg", "photo")])
        results.append(r)

        # --- DOCUMENT ---
        b = bases["document"]
        dn = wa_name(20, "DOC", b)
        # make a tiny pdf-like file
        pdf = OUT / "sample.pdf"; pdf.write_bytes(b"%PDF-1.4 fake pdf bytes")
        r = await run_one(client, peer, "doc", [
            f"{_wa(b)} John Doe: Before doc",
            f"{_wa(b + __import__('datetime').timedelta(seconds=30))} Jane Smith: <attached: {dn}>",
            f"{_wa(b + __import__('datetime').timedelta(seconds=50))} John Doe: After doc",
        ], [(dn, pdf, "document")])
        results.append(r)

        # --- VIDEO (use sticker webm as a stand-in tiny mp4-like) ---
        b = bases["video"]
        vn = wa_name(30, "VID", b)
        vp = OUT / ("src_vid_" + vn)
        shutil.copy(D / "sticker" / "sticker_160416.webm", vp)  # small webm used as sample bytes
        r = await run_one(client, peer, "vid", [
            f"{_wa(b)} John Doe: Before vid",
            f"{_wa(b + __import__('datetime').timedelta(seconds=30))} Jane Smith: <attached: {vn}>",
            f"{_wa(b + __import__('datetime').timedelta(seconds=50))} John Doe: After vid",
        ], [(vn, vp, "video")])
        results.append(r)

        # --- STICKER (webm as doc w/ sticker-ish) ---
        b = bases["sticker"]
        sn = wa_name(40, "STK", b)
        sp = OUT / ("src_stk_" + sn)
        shutil.copy(D / "sticker" / "sticker_160416.webm", sp)
        r = await run_one(client, peer, "stk", [
            f"{_wa(b)} John Doe: Before stk",
            f"{_wa(b + __import__('datetime').timedelta(seconds=30))} Jane Smith: <attached: {sn}>",
            f"{_wa(b + __import__('datetime').timedelta(seconds=50))} John Doe: After stk",
        ], [(sn, sp, "sticker")])
        results.append(r)

        # Space imports out: import_id expires if we start the next too fast.
        for i in range(3):
            await asyncio.sleep(30)
            print(f"--- spacing wait {i + 1}")

        print(json.dumps({
            "import_results": results,
        }, indent=2, default=str))

        # Poll for materialization, then inspect each block's photo/doc message
        for poll in range(8):
            await asyncio.sleep(25)
            print(f"--- poll {poll + 1}")
            for probe in ["After cap", "After doc", "After vid", "After stk"]:
                hits = await client.get_messages("pourfallah", search=probe, limit=3)
                if not hits:
                    continue
                m = hits[0]
                # the media message is the one right before "After X"
                prev = await client.get_messages("pourfallah", ids=[m.id - 1])
                p = prev[0] if prev and prev[0] else None
                if not p:
                    continue
                fwd = getattr(p, "fwd_from", None)
                media = type(p.media).__name__ if p.media else "None"
                attrs = ""
                if p.media and hasattr(p.media, "document") and p.media.document:
                    attrs = str([type(a).__name__ for a in getattr(p.media.document, "attributes", [])])
                print("  ", probe, "-> media msg id", p.id, "|",
                      media,
                      attrs,
                      "| text:", repr((p.message or "")[:30]),
                      "| imported:", getattr(fwd, "imported", None) if fwd else None)
    finally:
        await release()


if __name__ == "__main__":
    asyncio.run(main())
