"""Regression: verification report includes media classification & doesn't crash."""
from __future__ import annotations

from pathlib import Path

from app.services.import_verification import run_verification, write_report


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
    assert ms["restored"] == 1
    assert ms["total"] == 1
    # must not raise when writing the fidelity report
    from app.services.fidelity_report import build_fidelity_report

    out = tmp_path / "verif"
    out.mkdir()
    html = build_fidelity_report(report, out)
    assert html.exists() and html.read_text().startswith("<!DOCTYPE html>")