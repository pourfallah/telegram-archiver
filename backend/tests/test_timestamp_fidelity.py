"""Regression tests: timestamp fidelity of the import pipeline.

Guards against:
- timestamps converted incorrectly / timezone shifts
- messages reordered in the import file
- source dates replaced by current time
- media captions split into separate messages (formatting regression)
- sender name corruption
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.import_serializer import build_import_file


@pytest.fixture()
def archive(tmp_path: Path) -> Path:
    """Canonical archive with messages spanning years + a media message."""
    export_dir = tmp_path / "export"
    msgs_dir = export_dir / "archive" / "messages"
    msgs_dir.mkdir(parents=True)

    rows = [
        {"id": 30, "date": "2021-06-10T23:59:59+00:00", "sender": {"id": 2, "name": "Bob"}, "text": "later 2021", "media": []},
        # caption message: text belongs to the SAME logical message as its media
        {"id": 20, "date": "2020-01-02T15:30:00+00:00", "sender": {"id": 1, "name": "Alice"},
         "text": "photo caption", "media": [{"type": "photo", "filename": "p.jpg"}]},
        {"id": 40, "date": "2025-03-03T10:00:00+00:00", "sender": {"id": 1, "name": "Alice"}, "text": "2025 msg 😀", "media": []},
        {"id": 10, "date": "2020-01-01T10:00:00+00:00", "sender": {"id": 1, "name": "Alice"}, "text": "hello from 2020 🎉", "media": []},
        {"id": 50, "date": "2020-01-01T10:01:00+00:00", "sender": {"id": 2, "name": "Bob"}, "text": "", "media": [{"type": "video", "filename": "v.mp4"}]},
    ]
    with (msgs_dir / "messages.ndjson").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return export_dir


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def test_import_file_is_chronologically_ascending(archive, tmp_path):
    out = tmp_path / "import.txt"
    stats = build_import_file(archive, out)
    lines = _lines(out)
    dates = [ln.split(" - ")[0] for ln in lines if " - " in ln]
    parsed = [
        __import__("datetime").datetime.strptime(d, "%d.%m.%Y %H:%M")
        for d in dates
    ]
    assert parsed == sorted(parsed), f"lines not ascending: {dates}"
    assert stats["messages"] == 5


def test_timestamps_preserve_source_dates_not_now(archive, tmp_path):
    out = tmp_path / "import.txt"
    build_import_file(archive, out)
    content = out.read_text(encoding="utf-8")

    assert "01.01.2020 10:00" in content      # original historical date kept
    assert "02.01.2020 15:30" in content
    assert "10.06.2021 23:59" in content
    assert "03.03.2025 10:00" in content
    # no current-year stamp invented for old messages
    for ln in _lines(out):
        if "hello from 2020" in ln:
            assert ln.startswith("01.01.2020"), ln


def test_timezone_not_shifted_utc_written_verbatim(archive, tmp_path):
    out = tmp_path / "import.txt"
    build_import_file(archive, out)
    lines = _lines(out)
    assert any(ln.startswith("01.01.2020 11:00") is False and "10:00" in ln and "hello from 2020" in ln for ln in lines)


def test_media_line_shares_message_timestamp_and_sender(archive, tmp_path):
    out = tmp_path / "import.txt"
    build_import_file(archive, out)
    lines = _lines(out)
    # video-only message keeps its own line with same ts/sender
    assert any(
        ln.startswith("01.01.2020 10:01") and "Bob:" in ln and "<attached: v.mp4>" in ln
        for ln in lines
    )
    # caption stays attached to one logical message: photo line + caption line share ts/sender
    photo_lines = [ln for ln in lines if "<attached: p.jpg>" in ln]
    caption_lines = [ln for ln in lines if "photo caption" in ln]
    assert len(photo_lines) == 1 and len(caption_lines) == 1
    assert photo_lines[0].split(" - ")[0] == caption_lines[0].split(" - ")[0]


def test_emoji_preserved_exactly(archive, tmp_path):
    out = tmp_path / "import.txt"
    build_import_file(archive, out)
    assert "🎉" in out.read_text(encoding="utf-8")


def test_sender_names_intact(archive, tmp_path):
    out = tmp_path / "import.txt"
    build_import_file(archive, out)
    content = out.read_text(encoding="utf-8")
    assert "Alice:" in content and "Bob:" in content


def test_limit_takes_oldest_slice_in_order(archive, tmp_path):
    out = tmp_path / "import.txt"
    stats = build_import_file(archive, out, limit=3)
    lines = [ln for ln in _lines(out) if " - " in ln]
    assert stats["messages"] == 3
    # oldest three logical messages by date: 2020-01-01 10:00, 10:01, 2020-01-02 15:30
    first_ts = {ln.split(" - ")[0] for ln in lines}
    assert "01.01.2020 10:00" in first_ts
    assert not any(ln.startswith(("10.06.2021", "03.03.2025")) for ln in lines)
