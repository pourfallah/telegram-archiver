"""Minimal isolated import tests — one media type each, no full fixture.

These validate the SERIALIZER + upload attribute construction for the exact
syntax Telegram's import parser accepts (verified against tdlib
MessageImportManager.cpp and filippz/telegram_import).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.import_serializer import build_import_file


def _archive(tmp_path: Path, rows: list[dict]) -> Path:
    d = tmp_path / "export"
    mdir = d / "archive" / "messages"
    mdir.mkdir(parents=True)
    with (mdir / "messages.ndjson").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    return d


def _msg(mid: int, date: str, sender_id: int, name: str, text: str,
         media: list | None = None) -> dict:
    return {"id": mid, "date": date, "sender": {"id": sender_id, "name": name},
            "text": text, "media": media or []}


def test_import_text_only(tmp_path):
    """TEST 1 — text only: one timestamped line per message."""
    d = _archive(tmp_path, [
        _msg(1, "2024-01-01T10:00:00+00:00", 1, "A", "REPLY_PARENT"),
    ])
    out = tmp_path / "import.txt"
    stats = build_import_file(d, out)
    assert stats["messages"] == 1
    assert out.read_text(encoding="utf-8") == \
        "[01/01/2024, 10:00:00] - A: REPLY_PARENT\n"


def test_import_photo(tmp_path):
    """TEST 2 — photo only: marker line, no caption continuation."""
    d = _archive(tmp_path, [
        _msg(1, "2024-01-01T10:00:00+00:00", 1, "A", "", [
            {"type": "photo", "filename": "photo_test.jpg"}]),
    ])
    out = tmp_path / "import.txt"
    stats = build_import_file(d, out)
    assert stats["media_refs"] == 1
    content = out.read_text(encoding="utf-8")
    assert content == "[01/01/2024, 10:00:00] - A: <attached: photo_test.jpg>\n"


def test_import_photo_caption_one_block(tmp_path):
    """TEST 3 — photo+caption is ONE message block: caption is the physical
    line AFTER the marker line (no new timestamp prefix)."""
    d = _archive(tmp_path, [
        _msg(1, "2024-06-01T12:30:00+00:00", 1, "A", "CAPTION_TEST_123", [
            {"type": "photo", "filename": "cap.jpg"}]),
    ])
    build_import_file(d, tmp_path / "import.txt")
    raw = (tmp_path / "import.txt").read_text(encoding="utf-8").splitlines()
    assert len(raw) == 2, f"media+caption must be ONE block (2 physical lines), got {raw}"
    assert raw[0] == "[01/06/2024, 12:30:00] - A: <attached: cap.jpg>"
    assert raw[1] == "CAPTION_TEST_123"


def test_import_sticker_no_caption(tmp_path):
    """TEST 4 — sticker: document marker; attributes sent at upload time."""
    d = _archive(tmp_path, [
        _msg(3, "2026-08-26T05:17:13+00:00", 7, "First", "", [
            {"type": "sticker", "filename": "sticker.webp",
             "sticker_alt": "🐸", "width": 511, "height": 512}]),
    ])
    out = tmp_path / "import.txt"
    build_import_file(d, out)
    content = (tmp_path / "import.txt").read_text(encoding="utf-8")
    assert "<attached: sticker.webp>" in content


def test_upload_media_attributes():
    """uploadImportedMedia InputMedia carries source semantics:
    sticker -> DocumentAttributeSticker, audio -> DocumentAttributeAudio..."""
    from app.workers.import_tasks import _build_input_media

    class FakeClient:
        async def upload_file(self, path, file_name=None):  # noqa: ARG002
            return object()

    from telethon.tl.types import (
        DocumentAttributeAnimated,
        DocumentAttributeAudio,
        DocumentAttributeSticker,
        DocumentAttributeVideo,
        InputStickerSetEmpty,
    )

    sticker = _run(_build_input_media(FakeClient(), {
        "path": Path("/tmp/s.webp"), "type": "sticker", "mime": "image/webp",
        "extra": {"alt": "🐸", "width": 511, "height": 512}}))
    st_attrs = [a for a in sticker.attributes if isinstance(a, DocumentAttributeSticker)]
    assert st_attrs and st_attrs[0].alt == "🐸"
    assert any(isinstance(a.stickerset, InputStickerSetEmpty) for a in st_attrs)

    audio = _run(_build_input_media(FakeClient(), {
        "path": Path("/tmp/a.mp3"), "type": "audio", "mime": "audio/mpeg",
        "extra": {"duration": 274, "performer": "Hayedeh", "title": "To Ke Nisti"}}))
    au = [a for a in audio.attributes if isinstance(a, DocumentAttributeAudio)]
    assert au and au[0].performer == "Hayedeh" and au[0].title == "To Ke Nisti"

    video = _run(_build_input_media(FakeClient(), {
        "path": Path("/tmp/v.mp4"), "type": "animation", "mime": "video/mp4",
        "extra": {"duration": 1.0}}))
    assert any(isinstance(a, DocumentAttributeAnimated) for a in video.attributes)
    assert not any(isinstance(a, DocumentAttributeVideo) for a in video.attributes)


def _run(coro):
    import asyncio
    return asyncio.get_event_loop().run_until_complete(coro)


def test_mapping_never_positional_for_media():
    """Empty-text media without a compatible target stays UNMATCHED — never
    assigned positionally to another blank message."""
    from app.services.reconstruction import build_source_target_mapping, unmatched_sources

    src = [
        {"id": 1, "date": "2024-01-01T10:00:00+00:00", "text": "",
         "media": [{"type": "sticker"}]},
        {"id": 2, "date": "2024-01-01T10:00:01+00:00", "text": "",
         "media": [{"type": "photo"}]},
    ]
    # Only ONE blank target (a photo) exists — the sticker must stay unmatched.
    tgt = [{"id": 101, "date": "2026-08-25T09:00:01+00:00", "text": "",
            "target_media_raw": {"ctor": "MessageMediaPhoto", "attrs": []}}]
    mapping = build_source_target_mapping(src, tgt)
    assert mapping.get(2, {}).get("target_id") == 101 or 2 not in mapping
    assert 1 not in mapping  # sticker NOT assigned to the photo
    un = unmatched_sources(src, mapping)
    assert {"source_id": 1, "media_type": "sticker", "reason": "NOT_MATERIALIZED"} in un


@pytest.fixture(autouse=True)
def _no_event_loop_leak():
    yield
