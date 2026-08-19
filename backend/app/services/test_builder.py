"""Test migration package builder.

Generates a small WhatsApp-style import package (10/50/100/500/1000 messages)
with realistic senders, a date range, text, captions and real media samples —
guaranteeing the package satisfies the official importer's "must contain media"
requirement (see PROJECT_PLAN.md §9.6).
"""
from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.services.converter import build_whatsapp_package

ALLOWED_COUNTS = {10, 50, 100, 500, 1000}

# A 1x1 transparent PNG (68 bytes) — valid and tiny.
_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\x00"
    b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)

SENDERS = [
    {"id": 1, "name": "Alice", "username": "alice"},
    {"id": 2, "name": "Bob", "username": "bob"},
    {"id": 3, "name": "Carol", "username": "carol"},
]


def _message(i: int, start: datetime) -> dict:
    sender = SENDERS[i % len(SENDERS)]
    ts = start + timedelta(minutes=5 * i)
    m: dict = {
        "id": i + 1,
        "date": ts.isoformat(),
        "edited": None,
        "sender": sender,
        "text": f"Test message {i + 1} from {sender['name']}",
        "entities": [],
        "reply_to": None,
        "forwarded_from": None,
        "reactions": None,
        "views": None,
        "forwards": None,
        "media": [],
    }
    if i % 7 == 0:  # a media sample every 7th message
        m["media"] = [
            {
                "type": "photo",
                "mime_type": "image/png",
                "size_bytes": len(_PNG),
                "original_filename": f"photo_{i + 1}.png",
                "filename": f"photo_{i + 1}.png",
            }
        ]
    return m


def build_test_package(count: int, out_dir: Path) -> dict:
    """Build a WhatsApp-style test package with ``count`` messages."""
    if count not in ALLOWED_COUNTS:
        raise ValueError(f"count must be one of {sorted(ALLOWED_COUNTS)}")

    out_dir.mkdir(parents=True, exist_ok=True)
    start = datetime(2024, 1, 1, 9, 0, tzinfo=UTC)
    messages = [_message(i, start) for i in range(count)]

    # Build a throwaway export-style tree for the converter.
    work = out_dir / ".work"
    media_dir = work / "media" / "photo"
    media_dir.mkdir(parents=True, exist_ok=True)
    for m in messages:
        if m["media"]:
            (media_dir / m["media"][0]["filename"]).write_bytes(_PNG)
            m["media"][0]["file_path"] = f"media/photo/{m['media'][0]['filename']}"

    (work / "messages.json").write_text(
        json.dumps(
            {"schema_version": 1, "chat": {"title": "Test chat", "type": "group"}, "messages": messages},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    manifest = build_whatsapp_package(work, out_dir)
    shutil.rmtree(work, ignore_errors=True)
    return manifest
