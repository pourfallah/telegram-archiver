"""Single-pass 10-message MTProto micro-probe (media attachment + date check).

ONLINE-FREE fixture: sample_chat.txt has EXACTLY 10 messages (8 text, 1 photo
`<attached: test_photo.jpg>`, 1 video `<attached: test_video.mp4>`), all dated
January 2016 in Asia/Tehran local time with seconds. Two micro media files are
generated in a temp dir (a ~10KB photo PNG, a ~100KB video bytes).

Imports into A<->B via Account B with EXACTLY ONE initHistoryImport, exactly two
uploadImportedMedia (file_name BYTE-equal "test_photo.jpg" / "test_video.mp4"),
one startHistoryImport — all in ONE session. Then reads the target back via B and
reports whether each media message is a real MessageMedia (not text "<attached:…>")
and whether message.date preserves the 2016 Tehran timestamp.

Run when initHistoryImport is not flood-limited:
  python -m recovery_v2.import_ten_probe
(On FloodWaitError it prints the wait and exits non-zero; do not loop.)
"""
from __future__ import annotations

import asyncio
import json
import os
import struct
import sys
import tempfile
import zlib
from pathlib import Path
from zoneinfo import ZoneInfo

# --- hardcoded fixture: 10 messages, Jan-2016, Asia/Tehran local (standard +03:30) ---
FIXTURE = [
    "[02/01/2016, 09:05:07] First: Hello",
    "[02/01/2016, 09:06:12] Second: این یک پیام متنی است",
    "[05/01/2016, 14:22:30] First: بله درست است",
    "[08/01/2016, 18:47:01] Second: من موافقم",
    "[11/01/2016, 20:15:44] First: <attached: test_photo.jpg>",
    "[14/01/2016, 11:36:22] Second: متن دیگری",
    "[18/01/2016, 16:02:58] First: خوشحالم",
    "[22/01/2016, 19:41:33] Second: <attached: test_video.mp4>",
    "[27/01/2016, 08:19:17] First: ممنون",
    "[31/01/2016, 22:50:05] Second: پایان",
]
TEHRAN = ZoneInfo("Asia/Tehran")
TEHRAN_FMT = "%d/%m/%Y, %H:%M:%S"


def _fixture_ts(line: str) -> str:
    """Extract the bracketed Tehran-local timestamp 'DD/MM/YYYY, HH:MM:SS'."""
    return line[1:line.index("]")]


def make_png(w: int = 80, h: int = 60) -> bytes:
    """A VALID PNG in pure Python (random RGB) — ~a few KB, accepted as a photo."""
    px = bytearray(os.urandom(w * h * 3))
    raw = bytearray()
    for y in range(h):
        raw += b"\x00"
        raw += px[y * w * 3:(y + 1) * w * 3]

    def chunk(tag: bytes, data: bytes) -> bytes:
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw))) + chunk(b"IEND", b""))


def _date_preserved(utc_dt, fixture_ts_set: set[str]) -> bool:
    """Target message.date (UTC) re-rendered in Asia/Tehran must be a fixture ts."""
    if utc_dt is None:
        return False
    return utc_dt.astimezone(TEHRAN).strftime(TEHRAN_FMT) in fixture_ts_set


async def main() -> int:
    from recovery.config import load_dotenv
    from telethon import TelegramClient, errors
    from telethon.sessions import StringSession
    from telethon.tl import functions as f
    from telethon.tl import types as tl

    load_dotenv()
    api_id = int(os.environ["TELEGRAM_API_ID"])
    api_hash = os.environ["TELEGRAM_API_HASH"]

    from recovery_v2.login_accounts import AccountStore
    accounts = AccountStore().list()
    a_phone = next(x.get("phone") for x in accounts if (x.get("phone") or "").startswith("+98"))
    b_session = next(x["session_string"] for x in accounts
                     if not (x.get("phone") or "").startswith("+98"))

    tmp = Path(tempfile.mkdtemp(prefix="mprobe_"))
    chat_path = tmp / "sample_chat.txt"
    chat_path.write_text("\n".join(FIXTURE) + "\n", encoding="utf-8")
    photo_path = tmp / "test_photo.jpg"; photo_path.write_bytes(make_png())
    video_path = tmp / "test_video.mp4"; video_path.write_bytes(os.urandom(102400))  # ~100KB
    print(f"fixture: {len(FIXTURE)} msgs, photo {photo_path.stat().st_size}B, video {video_path.stat().st_size}B")

    report = {"step": {}, "new_messages": []}
    try:
        async with TelegramClient(StringSession(b_session), api_id, api_hash) as client:
            peer = await client.get_input_entity(a_phone)  # A<->B, resolved via B

            # A) upload the chat file
            chat_up = await client.upload_file(str(chat_path), file_name="sample_chat.txt")
            report["step"]["A_upload_chat"] = type(chat_up).__name__

            # B) checkHistoryImport (peer/content readiness; log only, do NOT gate)
            head = "\n".join(FIXTURE[:10])
            chk = await client(f.messages.CheckHistoryImportRequest(import_head=head))
            report["step"]["B_checkHistoryImport"] = {"pm": bool(getattr(chk, "pm", False)),
                                                      "group": bool(getattr(chk, "group", False))}

            # C) initHistoryImport — EXACTLY ONCE
            init = await client(f.messages.InitHistoryImportRequest(
                peer=peer, file=chat_up, media_count=2))
            import_id = int(init.id)
            report["step"]["C_initHistoryImport"] = import_id

            # D) uploadImportedMedia x2 — file_name BYTE-matches the <attached:> tokens
            ph = await client.upload_file(str(photo_path), file_name="test_photo.jpg")
            up_photo = await client(f.messages.UploadImportedMediaRequest(
                peer=peer, import_id=import_id, file_name="test_photo.jpg",
                media=tl.InputMediaUploadedPhoto(file=ph)))
            report["step"]["D_upload_photo"] = type(up_photo).__name__

            vh = await client.upload_file(str(video_path), file_name="test_video.mp4")
            up_video = await client(f.messages.UploadImportedMediaRequest(
                peer=peer, import_id=import_id, file_name="test_video.mp4",
                media=tl.InputMediaUploadedDocument(
                    file=vh, mime_type="video/mp4",
                    attributes=[tl.DocumentAttributeFilename("test_video.mp4"),
                                tl.DocumentAttributeVideo(duration=2, w=480, h=854)])))
            report["step"]["D_upload_video"] = type(up_video).__name__

            # E) startHistoryImport — EXACTLY ONCE
            started = await client(f.messages.StartHistoryImportRequest(peer=peer, import_id=import_id))
            report["step"]["E_startHistoryImport"] = bool(started)

            await asyncio.sleep(8.0)  # materialization lag

            # 3) VERIFY — read target via B
            offset_id, out = 0, []
            while True:
                r = await client(f.messages.GetHistoryRequest(
                    peer=peer, offset_id=offset_id, offset_date=None, add_offset=0,
                    limit=100, max_id=0, min_id=0, hash=0))
                ms = getattr(r, "messages", None) or []
                if not ms:
                    break
                out += ms
                if len(ms) < 100:
                    break
                offset_id = ms[-1].id
            imported = [m for m in out if getattr(getattr(m, "fwd_from", None), "imported", False)]
            report["new_count"] = len(imported)

            def ctor(m):
                med = getattr(m, "media", None)
                return type(med).__name__ if med is not None else "NONE"

            fixture_ts_set = {_fixture_ts(ln) for ln in FIXTURE}
            for m in sorted(imported, key=lambda x: x.id):
                text = (getattr(m, "message", None) or "")
                record = {
                    "id": m.id,
                    "media": ctor(m),
                    "media_is_text_marker": text.startswith("<attached:") or "(file attached)" in text,
                    "message_date_utc": str(getattr(m, "date", "")),
                    "message_date_tehran": getattr(m, "date", "").astimezone(TEHRAN).strftime(TEHRAN_FMT)
                        if getattr(m, "date", None) else None,
                    "text": text[:40],
                    "date_preserved": _date_preserved(getattr(m, "date", None), fixture_ts_set),
                }
                report["new_messages"].append(record)

            # summary
            media_ok = sum(1 for x in report["new_messages"] if x["media"] not in ("NONE",))
            date_ok = sum(1 for x in report["new_messages"] if x.get("date_preserved"))
            print(json.dumps(report, ensure_ascii=False, indent=2))
            print(f"\nMEDIA_ATTACHED={media_ok}/{len(imported)}  DATE_PRESERVED={date_ok}/{len(imported)}")
            return 0
    except errors.FloodWaitError as e:
        print(f"FLOODWAIT seconds={e.seconds} — do not retry now; run after it expires.")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))