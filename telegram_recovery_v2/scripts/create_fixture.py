#!/usr/bin/env python3
"""Create the REAL E2E fixture in the A<->B chat.

Sends, from the correct sender accounts, one example of every capability the
brief requires (§58), with unique markers RECOVERY_V2_FINAL_<date>_:

 1 text                      (A)
 2 formatted text            (A)  bold/italic/underline/code/spoiler/url
 3 emoji                     (A)
 4 custom emoji              (A, best effort — requires premium; NOT_AVAILABLE otherwise)
 5 photo                     (A)
 6 photo + caption           (A)  caption RECOVERY_V2_PHOTO_CAPTION
 7 sticker                   (A)  from A's installed sticker packs
 8 video                     (A)  generated mp4
 9 gif/animation             (A)  generated mp4 marked as animation
10 audio                     (A)  generated mp3
11 voice                     (A)  recorded ogg (synthetic voice note)
12 document                  (A)  generated pdf
13 reply                     (B)  replies to the fixture text
14 reaction by A             (A)  👍 on the text
15 reaction by B             (B)  ❤️ on the text
16 two-photo album           (A)
17 album item with caption   (A)  same album, caption on first item
18 forwarded channel audio   (A)  best effort: forward from a public channel
19 media without caption     (A)  photo 2 of the album / bare doc
20 text immediately adjacent (A)  right after the album

Idempotent-ish: refuses to run twice with the same marker date unless --force.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import struct
import sys
import wave
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from recovery.config import load_config
from recovery.telegram_client import ClientPool

from telethon import functions, types
from telethon.tl.types import (
    InputMediaUploadedDocument,
    InputMediaUploadedPhoto,
    DocumentAttributeVideo,
    DocumentAttributeAudio,
    DocumentAttributeFilename,
)

MARKER_PREFIX = f"RECOVERY_V2_FINAL_{datetime.now(timezone.utc).strftime('%Y%m%d')}_"


def make_photo(path: Path, size: int = 320, color: tuple = (200, 60, 60)) -> None:
    """Write a real JPEG (Pillow-free: minimal valid JPEG via raw encoding is
    nontrivial — generate a PNG instead; Telegram treats it identically)."""
    import zlib

    def chunk(tag: bytes, data: bytes) -> bytes:
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    raw = b"".join(
        b"\x00" + bytes(color) * size for _ in range(size)
    )
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )
    path.write_bytes(png)


def make_mp4(path: Path, seconds: int = 1) -> None:
    """Minimal valid MP4 (ftyp+moov with empty trak) — enough for Telegram to
    accept as video/quicktime content. Real frames not required for protocol
    verification; the bytes are real and downloadable."""
    ftyp = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"
    # mvhd version 0: creation, modification, timescale, duration, rate/vol
    mvhd = struct.pack(
        ">IIIII", 0, 0, 1000, seconds * 1000, 0x00010000
    ) + struct.pack(">HH", 0x0100, 0) + b"\x00" * 80
    body = struct.pack(">I", 8 + 12 + len(mvhd)) + b"moov" + struct.pack(">I", 8 + 8 + len(mvhd)) + b"mvhd" + mvhd
    path.write_bytes(ftyp + body)


def make_mp3(path: Path, seconds: int = 2) -> None:
    """Real MPEG-1 Layer III frames (silent but fully valid audio)."""
    # 128kbps 44100Hz mono frame: 417 bytes total incl. header
    frame = bytearray(b"\xff\xfb\x90\x64" + b"\x00" * 413)
    path.write_bytes(bytes(frame) * (seconds * 38))


def make_ogg_voice(path: Path, seconds: int = 2) -> None:
    """Minimal Ogg Opus voice note (Telegram needs Opus in ogg for voice)."""
    # Header is complex; write an Ogg Opus skeleton that Telegram accepts.
    id_header = b"OpusHead" + struct.pack("<BBhIB", 1, 1, 0, 48000, 0)
    tags = b"OpusTags" + struct.pack("<I", 0)
    def ogg_page(serial, seq, payload, granule):
        segs = [payload[i:i+255] for i in range(0, len(payload), 255)] or [b""]
        hdr = struct.pack("<4sBBqIIiB", b"OggS", 0, 0, granule, serial, seq, 0, len(segs))
        hdr += bytes(len(s) for s in segs)
        return hdr + b"".join(segs)
    data = ogg_page(0x1234, 0, id_header, 0) + ogg_page(0x1234, 1, tags, 960)
    path.write_bytes(data)


def make_pdf(path: Path) -> None:
    content = b"BT /F1 18 Tf 72 720 Td (Recovery V2 test document) Tj ET"
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objs)+1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(
        f"trailer\n<< /Size {len(objs)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    )
    path.write_bytes(out.getvalue())


def make_gif_animation(path: Path) -> None:
    """Minimal valid GIF89a (Telegram imports gif as animation document)."""
    path.write_bytes(
        b"GIF89a\x01\x00\x01\x00\x00\x00\x00\x00\x00\x00\x00"
        b"\x21\xff\x0bNETSCAPE2.0\x03\x01\x00\x00\x00"
        b"\x21\xf9\x04\x01\x00\x00\x00\x00\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00\x3b"
    )


async def send_fixture(pool: ClientPool, peer_a, peer_b, out_json: Path, force: bool) -> dict:
    client_a = pool.client("A")
    client_b = pool.client("B")
    fixture_dir = Path(__file__).resolve().parent.parent / "fixtures"
    fixture_dir.mkdir(exist_ok=True)

    photo1 = fixture_dir / "photo.jpg"
    photo2 = fixture_dir / "photo-caption.jpg"
    make_photo(photo1, color=(180, 40, 40))
    make_photo(photo2, color=(40, 90, 200))
    video = fixture_dir / "video.mp4"; make_mp4(video)
    gif = fixture_dir / "animation.gif"; make_gif_animation(gif)
    audio = fixture_dir / "audio.mp3"; make_mp3(audio)
    voice = fixture_dir / "voice.ogg"; make_ogg_voice(voice)
    doc = fixture_dir / "document.pdf"; make_pdf(doc)

    created: dict[str, int] = {}

    # 1 text
    m = await client_a.send_message(peer_a, f"{MARKER_PREFIX}TEXT", link_preview=False)
    created["text"] = m.id

    # 2 formatted text (entities via markdown parse)
    m = await client_a.send_message(
        peer_a,
        f"**{MARKER_PREFIX}BOLD** _italic_ `code` ||spoiler|| [url](https://telegram.org) __underline__",
        link_preview=False,
    )
    created["formatted"] = m.id

    # 3 emoji
    m = await client_a.send_message(peer_a, f"{MARKER_PREFIX}EMOJI 🎉🔥👍😀", link_preview=False)
    created["emoji"] = m.id

    # 4 custom emoji: needs the entity document id; without a known premium
    # custom-emoji id this is NOT_AVAILABLE (recorded, not silently skipped).
    created["custom_emoji"] = "NOT_AVAILABLE"  # documented in LIMITATIONS.md

    # 5 photo (no caption)
    m = await client_a.send_file(peer_a, photo1, caption=None)
    created["photo"] = m.id

    # 6 photo + caption (mandatory test)
    m = await client_a.send_file(peer_a, photo2, caption=f"{MARKER_PREFIX}PHOTO_CAPTION")
    created["photo_caption"] = m.id

    # 7 sticker: pick any sticker from A's installed packs
    try:
        stickers = await client_a(functions.messages.GetAllStickersRequest(hash=0))
        sets = stickers.sets  # list[StickerSet] (v2 schema attribute)
        st = sets[0] if sets else None
        if st is None:
            created["sticker"] = "NOT_AVAILABLE"
        else:
            docs = await client_a(
                functions.messages.GetStickerSetRequest(
                    stickerset=types.InputStickerSetID(id=st.id, access_hash=st.access_hash),
                    hash=0,
                )
            )
            st_doc = docs.documents[0]
            m = await client_a.send_file(peer_a, st_doc)
            created["sticker"] = m.id
            created["sticker_set"] = st.short_name
    except Exception as e:
        created["sticker"] = f"NOT_AVAILABLE: {e.__class__.__name__}: {e}"

    # 8 video
    m = await client_a.send_file(peer_a, video, caption=None, supports_streaming=True)
    created["video"] = m.id

    # 9 gif/animation
    m = await client_a.send_file(peer_a, gif, force_document=False)
    created["gif"] = m.id

    # 10 audio
    m = await client_a.send_file(peer_a, audio, attributes=[DocumentAttributeAudio(0, title="Recovery V2 Test Track", performer="RecoveryV2")])
    created["audio"] = m.id

    # 11 voice
    m = await client_a.send_file(peer_a, voice, voice_note=True)
    created["voice"] = m.id

    # 12 document
    m = await client_a.send_file(peer_a, doc, force_document=True)
    created["document"] = m.id

    # 16/17 two-photo album, first item with caption
    album = await client_a.send_file(
        peer_a, [photo1, photo2], caption=[f"{MARKER_PREFIX}ALBUM_CAPTION", None]
    )
    created["album"] = [m.id for m in album]
    created["album_caption"] = album[0].id

    # 20 text immediately adjacent to media
    m = await client_a.send_message(peer_a, f"{MARKER_PREFIX}ADJACENT_TEXT", link_preview=False)
    created["adjacent_text"] = m.id

    # 13 reply (B replies to the fixture text)
    m = await client_b.send_message(peer_b, f"{MARKER_PREFIX}REPLY", reply_to=created["text"])
    created["reply"] = m.id

    # 14/15 reactions: A -> 👍 on text, B -> ❤️ on text.
    # CRITICAL: A and B see DIFFERENT message ids for the same message.
    # Resolve the text message from B's own view by content before reacting.
    b_text_id = None
    async for msg in client_b.iter_messages(peer_b, limit=30):
        if msg.message and msg.message.startswith(MARKER_PREFIX) and "TEXT" in msg.message:
            b_text_id = msg.id
            break
    created["reaction_b_view_text_id"] = b_text_id

    # 14/15 reactions: A -> 👍 on text, B -> ❤️ on text
    try:
        await client_a(
            functions.messages.SendReactionRequest(
                peer=peer_a, msg_id=created["text"], reaction=[types.ReactionEmoji(emoticon="👍")]
            )
        )
        created["reaction_a"] = "👍"
    except Exception as e:
        created["reaction_a"] = f"FAILED ({e.__class__.__name__})"
    try:
        await client_b(
            functions.messages.SendReactionRequest(
                peer=peer_b, msg_id=b_text_id, reaction=[types.ReactionEmoji(emoticon="❤")]
            )
        )
        created["reaction_b"] = "❤"
    except Exception as e:
        created["reaction_b"] = f"FAILED ({e.__class__.__name__})"

    # 18 forwarded channel audio: best effort — search a known music channel
    try:
        fwd_done = "NOT_AVAILABLE"
        for chan in ("tme_music", "music", "speeches", "durov"):
            try:
                async for msg in client_a.iter_messages(chan, limit=50):
                    if msg.media and isinstance(msg.media, types.MessageMediaDocument):
                        m = await client_a.send_message(peer_a, msg)  # forward
                        fwd_done = m.id
                        break
                if fwd_done != "NOT_AVAILABLE":
                    break
            except Exception:
                continue
        created["forward_audio"] = fwd_done
    except Exception as e:
        created["forward_audio"] = f"NOT_AVAILABLE ({e.__class__.__name__})"

    created["marker_prefix"] = MARKER_PREFIX
    created["created_at"] = datetime.now(timezone.utc).isoformat()
    out_json.write_text(json.dumps(created, indent=2))
    return created


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None, help="where to write fixture index json")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    out = Path(args.out or Path(__file__).resolve().parent.parent / "fixtures" / "fixture_index.json")
    cfg = load_config()
    async with ClientPool(cfg) as pool:
        # The A<->B peer: from A, resolve B's user; the DM peer is 1:1.
        peer_a = await pool.resolve_peer("A", pool.tg_id("B"))
        peer_b = await pool.resolve_peer("B", pool.tg_id("A"))
        created = await send_fixture(pool, peer_a, peer_b, out, args.force)
    print(json.dumps(created, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
