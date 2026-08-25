"""Regression: verification report includes media classification & doesn't crash."""
from __future__ import annotations

from pathlib import Path

from app.services.import_verification import run_verification


def _src_dir(tmp_path: Path) -> Path:
    d = tmp_path / "archive"
    msgs = d / "messages"
    msgs.mkdir(parents=True)
    import json

    rows = [
        {"id": 1, "date": "2020-01-01T10:00:00+00:00", "sender": {"id": 1, "name": "A"},
         "text": "hi", "media": [{"type": "photo", "filename": "p.jpg"}]},
        {"id": 2, "date": "2020-01-01T10:01:00+00:00", "sender": {"id": 2, "name": "B"},
         "text": "yo", "media": []},
    ]
    with (msgs / "messages.ndjson").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    return d


def test_verification_handles_media_classification(tmp_path: Path):
    src = _src_dir(tmp_path)
    # target: photo restored (has_media_object True) + plain text match
    target = [
        {"date": "2020-01-01T10:00:00+00:00", "sender": {"id": 1, "name": "A"},
         "text": "hi", "media": [], "has_media_object": True},
        {"date": "2020-01-01T10:01:00+00:00", "sender": {"id": 2, "name": "B"},
         "text": "yo", "media": [], "has_media_object": False},
    ]
    report = run_verification(src, target)
    assert "media_classification" in report["details"]
    assert "media_summary" in report["details"]
    ms = report["details"]["media_summary"]
    # Only message 1 has source media; it restores (fallback path has_media_object).
    # The restored tally counts source-media messages that landed as a real object.
    assert ms["restored"] >= 1
    assert ms["total"] >= 1
    # honest classes present
    classes = {c["class"] for c in report["details"]["media_classification"]}
    assert classes  # non-empty
    # must not raise when writing the fidelity report
    from app.services.fidelity_report import build_fidelity_report

    out = tmp_path / "verif"
    out.mkdir()
    html = build_fidelity_report(report, out)
    assert html.exists() and html.read_text().startswith("<!DOCTYPE html>")


def test_verification_matches_by_imported_fwd_date(tmp_path: Path):
    """Imported target messages carry the true date in fwd_from — visible
    message.date may be import-time. That must be reported as
    IMPORTED_METADATA_ONLY, never TIMESTAMP_RESTORED or silent pass."""
    src = _src_dir(tmp_path)
    target = [
        # visible date is import-time (not 2020), but fwd_from.date is historical
        {"date": "2026-08-24T17:00:00+00:00", "sender": {"id": 1, "name": "A"},
         "text": "hi", "media": [], "has_media_object": True,
         "fwd_from": {"date": "2020-01-01T10:00:00+00:00", "imported": True}},
        {"date": "2026-08-24T17:00:01+00:00", "sender": {"id": 2, "name": "B"},
         "text": "yo", "media": [], "has_media_object": False,
         "fwd_from": {"date": "2020-01-01T10:01:00+00:00", "imported": True}},
    ]
    report = run_verification(src, target)
    assert report["counts"]["matched"] == 2
    # fwd_from.date matching is metadata-only, not restored
    for m in report["details"]["message_map"]:
        assert m["timestamp"] == "IMPORTED_METADATA_ONLY"
    # visible message.date was NOT restored -> timestamp check is false (honest)
    assert report["checks"]["timestamp"] is False
    assert report["overall"] in ("SOURCE_COVERED_METADATA_ONLY",)


def test_verification_positions_timeline_from_row_order(tmp_path: Path):
    """Source ordering must be preserved in the report's message_map (ascending)."""
    import json
    d = tmp_path / "archive"
    msgs = d / "messages"
    msgs.mkdir(parents=True)
    rows = [
        {"id": 1, "date": "2019-05-05T10:00:00+00:00", "sender": {"id": 9, "name": "Z"},
         "text": "first", "media": []},
        {"id": 2, "date": "2020-01-01T10:00:00+00:00", "sender": {"id": 9, "name": "Z"},
         "text": "second", "media": []},
    ]
    with (msgs / "messages.ndjson").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    target = [
        {"date": "2019-05-05T10:00:00+00:00", "sender": {"id": 3, "name": "Imp"},
         "text": "first", "media": [], "has_media_object": False,
         "fwd_from": {"date": "2019-05-05T10:00:00+00:00", "imported": True}},
        {"date": "2020-01-01T10:00:00+00:00", "sender": {"id": 3, "name": "Imp"},
         "text": "second", "media": [], "has_media_object": False,
         "fwd_from": {"date": "2020-01-01T10:00:00+00:00", "imported": True}},
    ]
    report = run_verification(d, target)
    # Sender remapped to importer (id 3) — honest attribution, not SENDER_MISMATCH
    for m in report["details"]["message_map"]:
        assert m["sender"] in ("SENDER_MAPPED_TO_IMPORTER", "SENDER_IDENTICAL")
    # timelines preserved as restored (visible date == source)
    assert report["checks"]["timestamp"] is True
    assert report["checks"]["sender"] is True


def test_verification_multi_field_not_text_only(tmp_path: Path):
    """A target message with the same text but a DIFFERENT source timestamp must
    not be counted as an EXACT (mapped) match — honest partial."""
    import json
    d = tmp_path / "archive"
    msgs = d / "messages"
    msgs.mkdir(parents=True)
    rows = [
        {"id": 1, "date": "2020-01-01T10:00:00+00:00", "sender": {"id": 1, "name": "A"},
         "text": "same", "media": []},
    ]
    with (msgs / "messages.ndjson").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    # target has same text but different date -> text_only match, timestamp NOT_RESTORED
    target = [
        {"date": "2023-09-09T09:00:00+00:00", "sender": {"id": 3, "name": "Imp"},
         "text": "same", "media": [], "has_media_object": False,
         "fwd_from": {"date": "2023-09-09T09:00:00+00:00", "imported": True}},
    ]
    report = run_verification(d, target)
    assert report["counts"]["matched"] == 1
    assert report["counts"]["matched_exact"] == 0
    assert report["counts"]["matched_text_only"] == 1
    assert report["details"]["wrong_timestamp"]  # NOT_RESTORED
    assert report["checks"]["timestamp"] is False


def test_verification_empty_text_media_not_swapped(tmp_path: Path):
    """A sticker and a photo both have empty text. Matching must disambiguate by
    media constructor so the sticker maps to MessageMediaDocument, not Photo."""
    import json
    d = tmp_path / "archive"
    msgs = d / "messages"
    msgs.mkdir(parents=True)
    rows = [
        {"id": 1, "date": "2020-01-01T10:00:00+00:00", "sender": {"id": 1, "name": "A"},
         "text": "", "media": [{"type": "sticker", "filename": "s.webp"}]},
        {"id": 2, "date": "2020-01-01T10:00:01+00:00", "sender": {"id": 1, "name": "A"},
         "text": "", "media": [{"type": "photo", "filename": "p.jpg"}]},
    ]
    with (msgs / "messages.ndjson").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    target = [
        {"id": 101, "date": "2026-08-25T09:00:00+00:00", "sender": {"id": 3, "name": "Imp"},
         "text": "", "media": [], "has_media_object": True,
         "target_media_raw": {"ctor": "MessageMediaPhoto", "attrs": [], "mime": None},
         "fwd_from": {"date": "2020-01-01T10:00:01+00:00", "imported": True}},
        {"id": 102, "date": "2026-08-25T09:00:01+00:00", "sender": {"id": 3, "name": "Imp"},
         "text": "", "media": [], "has_media_object": True,
         "target_media_raw": {"ctor": "MessageMediaDocument",
                              "attrs": ["DocumentAttributeFilename"], "mime": "image/webp"},
         "fwd_from": {"date": "2020-01-01T10:00:00+00:00", "imported": True}},
    ]
    report = run_verification(d, target)
    mm = report["details"]["message_map"]
    sticker = next(m for m in mm if m["source_id"] == 1)
    photo = next(m for m in mm if m["source_id"] == 2)
    # sticker -> document target; photo -> photo target (never swapped)
    assert sticker["target_id"] == 102
    assert photo["target_id"] == 101
