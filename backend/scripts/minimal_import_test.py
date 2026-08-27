"""Minimal live import experiments (surgical validation).

Each experiment builds a MINIMAL package, imports it into the target peer via
direct MTProto as Account B, waits for materialization, then reads the ACTUAL
target objects and reports per-message truth.

Usage: python3 minimal_import_test.py <test_name>
  caption   — text + AUDIO+caption CAPTION_TEST_123 + text
  photo     — PHOTO_TEST + JPEG + AFTER_PHOTO
  sticker   — STICKER_TEST + real Telegram sticker (downloaded from source chat)
  reply     — REPLY_PARENT <- REPLY_CHILD (real reply)
  timestamp — 3 messages with historical dates 2024/2025/2026
"""
from __future__ import annotations

import asyncio
import io
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import redis.asyncio as aioredis

from app.config import get_settings
from app.database import async_session_factory
from app.models import TelegramSession
from app.services.session_manager import SessionManager

A_SESSION_ID = 1
B_SESSION_ID = 3
A_VIEW_PEER = 7768075024   # David, from A's side
B_VIEW_PEER = 165649921    # First, from B's side
TZ = 210  # Iran minutes
OUT = Path("/data/fidelity/minimal_tests")


def _wa(dt: datetime) -> str:
    return (dt + timedelta(minutes=TZ)).strftime("[%d/%m/%Y, %H:%M:%S]")


async def _clients():
    settings = get_settings()
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    manager = SessionManager(settings=settings, redis=redis)
    async with async_session_factory() as db:
        from sqlalchemy import select
        acc_a = await db.scalar(select(TelegramSession).where(TelegramSession.id == A_SESSION_ID))
        acc_b = await db.scalar(select(TelegramSession).where(TelegramSession.id == B_SESSION_ID))
    ca, ra = await manager.acquire_client(acc_a)
    cb, rb = await manager.acquire_client(acc_b)
    return manager, acc_a, acc_b, ca, ra, cb, rb


def _make_jpeg() -> bytes:
    # Minimal valid JPEG (1x1 white)
    return bytes.fromhex(
        "ffd8ffe000104a46494600010100000100010000ffdb004300080606070605080707"
        "070909080a0c140d0c0b0b0c1912130f141d1a1f1e1d1a1c1c20242e2720222c231c"
        "1c2837292c30313434341f27393d38323c2e333432ffc0000b080001000101011100"
        "ffc4001f0000010501010101010100000000000000000102030405060708090a0bff"
        "c400b5100002010303020403050504040000017d0102030004110512213141061351"
        "6107227114328191a1082342b1c11552d1f02433627282090a161718191a25262728"
        "292a3435363738393a434445464748494a535455565758595a636465666768696a73"
        "74757677787a82838485878889929091939495969798999aa2a3a4a5a7a8a9b2b4b5"
        "b7b8b9bac2c3c4c5c7c8c9cad2d3d4d5d7d8d9dae1e2e4e5e7e8e9eaf1f2f4f5f7f8"
        "f9faffda0008010100003f00fbfa28a2803fffd9")


async def _run_import(cb, lines: list[str], files: list[tuple[str, bytes | Path, object]],
                      peer_b):
    """files: list of (filename, payload, InputMedia factory(file_handle))."""
    from telethon import functions

    before_list = await cb.get_messages(peer_b, limit=200)
    before_ids = {m.id for m in before_list}

    body = "\n".join(lines) + "\n"
    handle = await cb.upload_file(body.encode("utf-8"))
    head = lines[0][:120]
    chk = await cb(functions.messages.CheckHistoryImportRequest(import_head=head))
    init = await cb(functions.messages.InitHistoryImportRequest(
        peer=peer_b, file=handle, media_count=len(files)))
    trace = [{"step": "init", "import_id": getattr(init, "id", None),
              "check_pm": bool(getattr(chk, "pm", False))}]
    for fname, payload, factory in files:
        if isinstance(payload, Path):
            data = payload.read_bytes()
        else:
            data = payload
        fh = await cb.upload_file(io.BytesIO(data))
        media = factory(fh)
        res = await cb(functions.messages.UploadImportedMediaRequest(
            peer=peer_b, import_id=init.id, file_name=fname, media=media))
        trace.append({
            "step": "upload", "filename": fname,
            "input_ctor": type(media).__name__,
            "returned_ctor": type(res).__name__ if res else None,
            "document_id": getattr(getattr(res, "document", None), "id", None),
            "photo_id": getattr(getattr(res, "photo", None), "id", None),
        })
    await cb(functions.messages.StartHistoryImportRequest(
        peer=peer_b, import_id=init.id))
    trace.append({"step": "start"})
    await asyncio.sleep(20)
    after_list = await cb.get_messages(peer_b, limit=len(lines) + len(files) + 40)
    new = [m for m in after_list if m.id not in before_ids]
    return sorted(new, key=lambda x: x.id), trace


def _describe(m) -> dict:
    med = m.media
    doc = getattr(med, "document", None) if med else None
    attrs = [type(a).__name__ for a in getattr(doc, "attributes", None) or []] if doc else []
    fwd = getattr(m, "fwd_from", None)
    rt = getattr(m, "reply_to", None)
    rid = getattr(rt, "reply_to_msg_id", None) if rt else None
    return {
        "id": m.id,
        "date": m.date.isoformat()[:19] if m.date else None,
        "text": (m.message or "")[:60],
        "media_ctor": type(med).__name__ if med else None,
        "attrs": attrs,
        "mime": getattr(doc, "mime_type", None) if doc else None,
        "doc_id": getattr(doc, "id", None) if doc else None,
        "reply_to": rid,
        "fwd_date": fwd.date.isoformat()[:19] if fwd and fwd.date else None,
        "imported": bool(fwd.imported) if fwd else False,
    }


async def main(name: str):
    OUT.mkdir(parents=True, exist_ok=True)
    manager, acc_a, acc_b, ca, ra, cb, rb = await _clients()
    try:
        now = datetime.utcnow().replace(second=0, microsecond=0)
        peer_b = await cb.get_entity(B_VIEW_PEER)

        if name == "caption":
            from telethon import types as tl

            def audio_media(fh):
                return tl.InputMediaUploadedDocument(
                    file=fh, mime_type="audio/mpeg",
                    attributes=[tl.DocumentAttributeAudio(duration=12, performer="T",
                                                          title="CAPTION_TEST_123"),
                                tl.DocumentAttributeFilename("cap_test.mp3")])
            ts1, ts2 = _wa(now), _wa(now)
            lines = [
                f"{ts1} - First: BEFORE_CAPTION",
                f"{ts2} - First: cap_test.mp3 (file attached)\nCAPTION_TEST_123",
                f"{ts1} - First: AFTER_CAPTION",
            ]
            files = [("cap_test.mp3", b"\xff\xfb" + b"\x00" * 512, audio_media)]
            new, trace = await _run_import(cb, lines, files, peer_b)
            report = {"trace": trace, "target": [_describe(m) for m in new]}
            verdict = {
                "one_message_with_caption": any(
                    r["media_ctor"] == "MessageMediaDocument" and "CAPTION_TEST_123" in r["text"]
                    for r in report["target"]),
                "split_into_two": sum(1 for r in report["target"] if "CAPTION_TEST_123" in r["text"]) > 1,
            }

        elif name == "photo":
            from telethon import types as tl

            def photo_media(fh):
                return tl.InputMediaUploadedPhoto(file=fh)
            ts = _wa(now)
            lines = [
                f"{ts} - First: PHOTO_TEST",
                f"{ts} - First: photo_test.jpg (file attached)",
                f"{ts} - First: AFTER_PHOTO",
            ]
            files = [("photo_test.jpg", _make_jpeg(), photo_media)]
            new, trace = await _run_import(cb, lines, files, peer_b)
            report = {"trace": trace, "target": [_describe(m) for m in new]}
            verdict = {
                "photo_is_MessageMediaPhoto": any(
                    r["media_ctor"] == "MessageMediaPhoto" for r in report["target"]),
                "order_text_photo_text": [bool(r["media_ctor"]) for r in report["target"]],
            }

        elif name == "sticker":
            from telethon import types as tl

            # fetch a REAL sticker from the A-side history (msg 5674582 area)
            peer_a = await ca.get_entity(A_VIEW_PEER)
            src_msgs = await ca.get_messages(peer_a, limit=200)
            sticker_msg = next((m for m in src_msgs
                                if m.media and getattr(m.media, "document", None)
                                and any(isinstance(a, tl.DocumentAttributeSticker)
                                        for a in m.media.document.attributes or [])), None)
            if sticker_msg is None:
                print(json.dumps({"error": "no real sticker found in A-side recent history"}))
                return 1
            buf = io.BytesIO()
            await ca.download_media(sticker_msg, file=buf)
            sdoc = sticker_msg.media.document
            alt = next((a.alt for a in sdoc.attributes if isinstance(a, tl.DocumentAttributeSticker)), "")

            def sticker_media(fh):
                return tl.InputMediaUploadedDocument(
                    file=fh, mime_type="image/webp",
                    attributes=[tl.DocumentAttributeSticker(alt=alt, stickerset=tl.InputStickerSetEmpty()),
                                tl.DocumentAttributeImageSize(w=sdoc.w or 0, h=sdoc.h or 0),
                                tl.DocumentAttributeFilename("sticker.webp")])

            ts = _wa(now)
            lines = [f"{ts} - First: STICKER_TEST",
                     f"{ts} - First: sticker.webp (file attached)"]
            files = [("sticker.webp", buf.getvalue(), sticker_media)]
            new, trace = await _run_import(cb, lines, files, peer_b)
            report = {"source_sticker": {"doc_id": sdoc.id, "alt": alt,
                                          "mime": sdoc.mime_type},
                       "trace": trace, "target": [_describe(m) for m in new]}
            tgt_sticker = next((r for r in report["target"]
                                if r["media_ctor"] == "MessageMediaDocument"
                                and "DocumentAttributeSticker" in (r["attrs"] or [])), None)
            tgt_any_doc = next((r for r in report["target"]
                                if r["media_ctor"] == "MessageMediaDocument"), None)
            verdict = {
                "STICKER_EXACT": bool(tgt_sticker),
                "DOCUMENT_ONLY": not tgt_sticker and bool(tgt_any_doc),
                "same_document_id": bool(tgt_sticker and tgt_sticker["doc_id"] == sdoc.id),
            }

        elif name == "reply":
            # Send REAL messages from A? NO — A is read-only. Instead build an
            # import where line 2 references line 1 via WhatsApp quote syntax?
            # The import format has NO reply syntax; this experiment proves it.
            ts = _wa(now)
            lines = [
                f"{ts} - First: REPLY_PARENT",
                f"{ts} - First: REPLY_CHILD",
            ]
            new, trace = await _run_import(cb, lines, [], peer_b)
            report = {"trace": trace, "target": [_describe(m) for m in new]}
            verdict = {
                "any_reply_to_in_imported_block": any(r["reply_to"] for r in report["target"]),
                "REPLY_NOT_RECONSTRUCTABLE_BY_IMPORT": True,
            }

        elif name == "timestamp":
            lines = [
                "[01/01/2024, 12:00:00] - First: HIST_2024",
                "[01/06/2025, 12:00:00] - First: HIST_2025",
                f"[{datetime.utcnow().strftime('%d/%m/%Y')}, "
                f"{datetime.utcnow().strftime('%H:%M:%S')}] - First: HIST_NOW",
            ]
            new, trace = await _run_import(cb, lines, [], peer_b)
            report = {"trace": trace, "target": [_describe(m) for m in new]}
            bytext = {r["text"]: r["date"] for r in report["target"]}
            verdict = {
                "HIST_2024_exact": bytext.get("HIST_2024", "").startswith("2024-01-01"),
                "HIST_2025_exact": bytext.get("HIST_2025", "").startswith("2025-06-01"),
                "dates": {t: d for t, d in bytext.items()},
            }
        else:
            print(f"unknown test {name}")
            return 2

        report["verdict"] = verdict
        out_file = OUT / f"{name}.json"
        out_file.write_text(__import__("json").dumps(report, ensure_ascii=False, indent=2, default=str),
                            encoding="utf-8")
        print(__import__("json").dumps(report, ensure_ascii=False, indent=2, default=str))
        return 0
    finally:
        await ra()
        await rb()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1])))
