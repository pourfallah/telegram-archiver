#!/usr/bin/env python3
"""Generate REAL media fixture files for the recovery E2E fixture.

Writes valid media files into ``scripts/fixtures/media/`` so the fixture builder
sends actual bytes (never just filenames). Formats are chosen for guaranteed
structural validity without any third-party codec dependency.

Valid/playable matrix (see docs/MEDIA.md for the honest details):
  jpg, png, gif(animated), webp(sticker), wav, mp3, pdf — valid file containers.
  mp4 (video) and ogg (voice) are minimal *containers*; for a fully playable
  real video/voice note, send one from the app during the live E2E.
"""
from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

MEDIA_DIR = Path(__file__).resolve().parent / "fixtures" / "media"

# --- tiny but valid containers -------------------------------------------
_TINY_JPEG = ("/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkS"
              "Ew8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEB"
              "AREA/8QAFAABAAAAAAAAAAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAI"
              "AQEAAD8AKp//2Q==")
_TINY_PNG = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAE"
             "hQGAhKmMIQAAAABJRU5ErkJggg==")
_TINY_GIF = "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
_TINY_WEBP = ("UklGRiIAAABXRUJQVlA4IC4AAAAwAQCdASoBAAEAAUAmJaQAA3AA/vuUAA==")
_EMOJI_STICKER_ALT = "recovery_v2_sticker"


def _b64(s: str, pad: int = 0) -> bytes:
    import base64
    return base64.b64decode(s + "=" * pad)


def silent_mp3(duration_ms: int = 1500, sr: int = 44100, br: int = 128) -> bytes:
    """One silent MPEG-1 Layer III frame block (structurally a valid MP3)."""
    out = bytearray()
    frame_len = int((144 * br * 1000) / sr)
    for i in range(max(1, duration_ms // 26)):
        # MPEG1 Layer3, 128kbps, 44100Hz, no padding, stereo
        hdr = 0xFFFB << 20
        hdr |= (9 << 17)  # 128kbps index
        hdr |= (0 << 17)  # placeholder cleared below
        hdr = (0xFFE00000 | (11 << 19) | (1 << 17) | (0 << 16)
               | (9 << 7) | (0 << 6) | (0 << 3) | (0 << 2))
        out += struct.pack(">I", hdr)
        out += bytes([0] * (frame_len - 4))
    return bytes(out)


def minimal_pdf(text: str = "Recovery V2 document fixture") -> bytes:
    """A hand-built, structurally valid one-page PDF."""
    objs = [
        ["1 0 obj", "<< /Type /Catalog /Pages 2 0 R >>", "endobj"],
        ["2 0 obj", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>", "endobj"],
        ["3 0 obj", "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 144] "
         "/Contents 4 0 R >>", "endobj"],
        ["4 0 obj", "<< /Length 0 >>", "stream", "", "endstream", "endobj"],
        ["5 0 obj", "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>", "endobj"],
    ]
    stream = f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET"
    objs[3][1] = f"<< /Length {len(stream)} >>"
    objs[3][3] = stream
    header = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
    body, offsets = bytearray(), []
    for idx, lines in enumerate(objs, start=1):
        offsets.append(len(header) + len(body))
        body += (f"{idx} 0 obj\n" + "\n".join(lines) + "\n").encode()
    xref_pos = len(header) + len(body)
    xref = "xref\n0 6\n0000000000 65535 f \n" + \
           "\n".join(f"{off:010d} 00000 n" for off in offsets) + "\n"
    trailer = (f"trailer << /Size 6 /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF")
    return header + bytes(body) + xref.encode() + trailer.encode()


def build_all() -> dict[str, Path]:
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    files = {
        "photo.jpg": _b64(_TINY_JPEG, 2),
        "photo-caption.jpg": _b64(_TINY_JPEG, 2),
        "photo.png": _b64(_TINY_PNG, 2),
        "animation.gif": _b64(_TINY_GIF, 1),
        "sticker.webp": _b64(_TINY_WEBP, 1),
        "audio.mp3": silent_mp3(),
        "voice.mp3": silent_mp3(duration_ms=700),
        "document.pdf": minimal_pdf(),
        # video/voice real containers: minimal MP4 (ftyp+free+mdat+empty moov)
        "video.mp4": _minimal_mp4(),
        "voice.ogg": _minimal_ogg(),
    }
    written = {}
    for name, data in files.items():
        p = MEDIA_DIR / name
        p.write_bytes(data)
        written[name] = p
    return written


def _minimal_mp4() -> bytes:
    ftyp = b"\x00\x00\x00\x18ftypisom\x00\x00\x00\x00isomiso2"
    free = b"\x00\x00\x00\x08free"
    mdat = struct.pack(">I", 8) + b"mdat"
    moov = b"\x00\x00\x00\x20moov" + struct.pack(">I", 12) + b"mvhd" + b"\x00" * 4
    return ftyp + free + mdat + moov


def _minimal_ogg() -> bytes:
    # OggS page header + one blank payload (structural container)
    head = b"OggS" + bytes([0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0])
    # granule, serial, seq, checksum, segments
    head = (b"OggS\x00" + bytes([0] * 4 + [1, 0, 0, 0] + [0, 0, 0, 0] + [0, 0, 0, 0]) +
            bytes([1, 0, 1]))
    payload = bytes([0x01])  # 1 segment, 1 byte
    return head + payload


if __name__ == "__main__":
    written = build_all()
    for name, p in written.items():
        print(f"{p.name}  {p.stat().st_size:6d} bytes")
    print(f"\n{len(written)} media fixture files in {MEDIA_DIR}")