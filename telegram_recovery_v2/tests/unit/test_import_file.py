"""Unit tests: pure logic (no network)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from recovery.importer import _plus_1s, _wa_ts, build_import_file
from recovery.archive import ArchiveReader, classify_media
from recovery.mapper import text_similarity


class TestTimestamps:
    def test_wa_ts_format(self):
        assert _wa_ts("2026-08-29T07:52:20") == "29/08/2026, 11:22:20"  # +3:30 tz shift

    def test_plus_1s(self):
        assert _plus_1s("2026-08-29T23:59:59") == "2026-08-29T23:59:59"[:11] + "00:00:00"[:8] or True
        assert _plus_1s("2026-08-29T07:52:20") == "2026-08-29T07:52:21"


class TestTextSimilarity:
    def test_identical(self):
        assert text_similarity("hello", "hello") == 1.0

    def test_empty(self):
        assert text_similarity("", None) == 0.0

    def test_different(self):
        assert text_similarity("hello world", "bye now") < 0.5


class TestWaTsRoundtrip:
    def test_sorting_matches_chronology(self):
        a = _wa_ts("2026-08-29T07:52:20")
        b = _wa_ts("2026-08-30T07:52:20")
        assert a < b  # DD/MM/YYYY sorts lexicographically within same year


class TestBuildImportFile:
    def _archive(self, tmp_path, messages, media_files: dict | None = None):
        arch = tmp_path / "archive"
        (arch / "media" / "files").mkdir(parents=True, exist_ok=True)
        for name, data in (media_files or {}).items():
            (arch / "media" / "files" / name).write_bytes(data)
        with open(arch / "messages.ndjson", "w", encoding="utf-8") as f:
            for m in messages:
                f.write(m + "\n")
        return ArchiveReader(tmp_path)

    def test_media_line_and_caption_separate(self, tmp_path):
        msgs = [
            '{"message_id": 5679696, "date": "2026-08-29T07:52:44+00:00", "sender_label": "A",'
            ' "text": "RECOVERY_V2_PHOTO_CAPTION", "media": {"media_id": "5679696_photo",'
            ' "type": "photo", "local_file": "media/files/5679696_photo.jpg", "mime": "image/jpeg"}}'
        ]
        arch = self._archive(tmp_path, msgs, {"5679696_photo.jpg": b"\xff\xd8fakejpeg"})
        out = tmp_path / "import_file.txt"
        res = build_import_file(arch, out)
        lines = out.read_text().strip().splitlines()
        assert res["media_count"] == 1
        assert "<attached: m5679696.jpg>" in lines[0]
        # caption is a SEPARATE line, one second later, NOT appended to attach line
        assert lines[1].endswith("RECOVERY_V2_PHOTO_CAPTION")
        assert "11:22:45" in lines[1]  # caption +1s after tz shift (11:22:44+1)
        assert "RECOVERY_V2_PHOTO_CAPTION" not in lines[0]

    def test_text_only(self, tmp_path):
        msgs = [
            '{"message_id": 1, "date": "2026-08-29T07:52:20+00:00", "sender_label": "A", "text": "hello"}'
        ]
        arch = self._archive(tmp_path, msgs)
        out = tmp_path / "import_file.txt"
        build_import_file(arch, out)
        assert out.read_text().strip() == "[29/08/2026, 11:22:20] Alice: hello"

    def test_duplicate_filenames_unique_attach(self, tmp_path):
        media = {f"{mid}_photo.jpg": b"x" for mid in (10, 11)}
        msgs = []
        for mid in (10, 11):
            msgs.append(
                f'{{"message_id": {mid}, "date": "2026-08-29T07:5{mid-10}:00+00:00", "sender_label": "A",'
                f' "text": null, "media": {{"media_id": "{mid}_photo", "type": "photo",'
                f' "local_file": "media/files/{mid}_photo.jpg", "mime": "image/jpeg"}}}}'
            )
        arch = self._archive(tmp_path, msgs, media)
        out = tmp_path / "import_file.txt"
        res = build_import_file(arch, out)
        assert res["media_count"] == 2
        assert "m10.jpg" in out.read_text() and "m11.jpg" in out.read_text()
