"""Integration tests: mapper + verifier classification logic (offline)."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from recovery.mapper import map_source_to_target
from recovery.verifier import (
    classify_caption,
    classify_sender,
    classify_timestamp,
    classify_media_target,
)


class FakeMsg:
    def __init__(self, id, date, message="", media=None, fwd=None, reply_to=None):
        self.id = id
        self.date = date
        self.message = message
        self.media = media
        self.fwd_from = fwd
        self.reply_to = reply_to


class TestMapper:
    def test_delta_only_and_ordering(self, tmp_path):
        arch = tmp_path / "archive"
        arch.mkdir()
        srcs = [
            {"message_id": 1, "date": "2026-08-29T07:00:00+00:00", "text": "hello", "media": None},
            {"message_id": 2, "date": "2026-08-29T07:01:00+00:00", "text": "world", "media": None},
        ]
        with open(arch / "messages.ndjson", "w") as f:
            for s in srcs:
                f.write(json.dumps(s) + "\n")
        after = [
            {"message_id": 100, "date": "2026-08-29T07:00:01+00:00", "text": "hello", "media": None},
            {"message_id": 101, "date": "2026-08-29T07:01:01+00:00", "text": "world", "media": None},
            {"message_id": 50, "date": "2026-08-28T00:00:00+00:00", "text": "old pre-existing", "media": None},
        ]
        ap = tmp_path / "target_after.json"
        with open(ap, "w") as f:
            for t in after:
                f.write(json.dumps(t) + "\n")
        res = map_source_to_target(arch, ap, {50}, tmp_path)
        assert res["mapped"] == 2
        by_src = {m["source_message_id"]: m["target_message_id"] for m in res["mappings"]}
        assert by_src == {1: 100, 2: 101}


class TestCaptionClassify:
    def test_attached(self):
        s = {"text": "cap", "media": {"type": "photo"}}
        t = FakeMsg(1, None, message="cap", media=object())
        assert classify_caption(s, t) == "CAPTION_ATTACHED"

    def test_separate(self):
        s = {"text": "cap", "media": {"type": "photo"}}
        t = FakeMsg(1, None, message="cap", media=None)
        assert classify_caption(s, t) == "CAPTION_SEPARATE"

    def test_lost(self):
        s = {"text": "cap", "media": {"type": "photo"}}
        t = FakeMsg(1, None, message="different", media=None)
        assert classify_caption(s, t) == "CAPTION_LOST"


class TestSenderClassify:
    def _fwd(self, imported=True):
        class F:
            pass
        F.imported = imported
        return F()

    def test_metadata_only(self):
        s = {"sender_label": "A"}
        t = FakeMsg(1, None, fwd=self._fwd(True))
        assert classify_sender(s, t) == "SENDER_METADATA_ONLY"

    def test_exact_for_b(self):
        s = {"sender_label": "B"}
        t = FakeMsg(1, None, fwd=None)
        assert classify_sender(s, t) == "SENDER_EXACT"


class TestTimestampClassify:
    def test_exact_same_day(self):
        s = {"date": "2026-08-29T07:52:20+00:00"}
        from datetime import datetime

        t = FakeMsg(1, datetime.fromisoformat("2026-08-29T07:52:21+00:00"))
        assert classify_timestamp(s, t) == "TIMESTAMP_EXACT"

    def test_metadata_only(self):
        s = {"date": "2026-01-01T00:00:00+00:00"}
        from datetime import datetime

        class F:
            date = datetime.fromisoformat("2026-01-01T00:00:00+00:00")

        t = FakeMsg(1, datetime.fromisoformat("2026-08-29T09:00:00+00:00"), fwd=F())
        assert classify_timestamp(s, t) == "IMPORTED_METADATA_ONLY"
