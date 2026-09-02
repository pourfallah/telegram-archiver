"""Import package builder + ImportEngine RPC flow."""
from __future__ import annotations

import json

from recovery.archive import Archive
from recovery.importer import (ImportEngine, _date_str, build_import_package,
                               tehran_local_of, tehran_timestamp_checks)
from tests.fakes import FakeClient, FakeRecoveryClient


def _archive_with_photo(tmp_path) -> Archive:
    a = Archive(tmp_path / "archive")
    a.create()
    a.write_manifest({"run_id": "x"})
    media_dir = a.media_dir
    (media_dir / "1__0__photo.jpg").write_bytes(b"JPEGBYTES")
    rec = {
        "schema_version": 1, "source_message_id": 1,
        "date": "2026-08-01T10:00:00+00:00", "text": "gooood music 😍",
        "caption": "gooood music 😍", "media": [
            {"path": "media/1__0__photo.jpg", "sha256": "abc", "filename": "photo.jpg",
             "type": "photo", "constructor": "MessageMediaPhoto"}],
        "entities": [], "reply_to": None, "forward": None, "grouped_id": None,
        "from_id": {"user_id": 100}, "reactions": None,
    }
    rec2 = {"schema_version": 1, "source_message_id": 2,
            "date": "2026-08-01T10:00:01+00:00", "text": "plain text", "caption": None,
            "media": [], "entities": [], "reply_to": None, "forward": None,
            "grouped_id": None, "from_id": {"user_id": 100}, "reactions": None}
    for r in (rec, rec2):
        a.append_canonical(r)
        a.append_raw({"id": r["source_message_id"]})
    return a


def test_build_import_package_direct_from_archive(tmp_path):
    a = _archive_with_photo(tmp_path)
    out = tmp_path / "package"
    stats = build_import_package(a, out)
    assert stats["messages"] == 2 and stats["media"] == 1
    chat = (out / "_chat.txt").read_text(encoding="utf-8")
    assert "<Attached: photo.jpg>" in chat
    assert "gooood music 😍" in chat
    assert (out / "media" / "photo.jpg").read_bytes() == b"JPEGBYTES"
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["rows"][0]["source_message_id"] == 1
    assert manifest["rows"][0]["media"][0]["file"] == "photo.jpg"


def test_import_engine_rpc_order_and_resume(tmp_path):
    a = _archive_with_photo(tmp_path)
    pkg_out = tmp_path / "package"
    build_import_package(a, pkg_out)

    fake_tgt = FakeClient(history=[], import_id=909)
    src = FakeRecoveryClient(FakeClient(), my_id=100)
    tgt = FakeRecoveryClient(fake_tgt, my_id=200)
    # run_dir=tmp_path so ImportEngine's "<run>/package/media" resolves to pkg_out/media
    eng = ImportEngine(src, tgt, "peer", tmp_path)
    eng._set_package_media(pkg_out)
    eng._package_media_files = ["photo.jpg"]

    # simulate init already done -> resume path must NOT re-init
    state = {"import_id": 909}
    outcome = await_import(eng, pkg_out, state)
    assert outcome.import_id == 909
    # initHistoryImport must NOT appear in calls (already inited)
    assert "InitHistoryImportRequest" not in fake_tgt.calls
    assert "UploadImportedMediaRequest" in fake_tgt.calls
    assert "StartHistoryImportRequest" in fake_tgt.calls
    # media actually uploaded (file existed on disk)
    assert any(mt.upload_rpc_ok for mt in outcome.media_traces)
    assert len(fake_tgt.uploaded) == 1

    # fresh state -> full sequence
    fake2 = FakeClient(history=[], import_id=1234)
    eng2 = ImportEngine(src, FakeRecoveryClient(fake2, my_id=200), "peer", tmp_path)
    eng2._package_media_files = ["photo.jpg"]
    o2 = await_import(eng2, pkg_out, {})
    order = o2.rpc_order
    assert order[0] == "checkHistoryImport"
    assert order[1] == "checkHistoryImportPeer"
    assert order[2] == "initHistoryImport"
    assert order[-1] == "startHistoryImport"
    assert o2.import_id == 1234
    assert any(len(mt.note) >= 0 for mt in o2.media_traces)


def await_import(eng, pkg, state):
    import asyncio
    return asyncio.run(eng.run_import(pkg, state))


def test_tehran_timestamp_roundtrip():
    """Asia/Tehran historical encoding has NO timezone shift (minute-exact)."""
    checks = tehran_timestamp_checks()
    assert len(checks) == 3
    assert all(c["minute_exact"] for c in checks), checks
    by_src = {c["source_utc"]: c for c in checks}

    # source 5307: 2015-12-31T20:35:57Z -> Tehran 2016-01-01 00:05:57 +03:30
    c5307 = by_src["2015-12-31T20:35:57+00:00"]
    assert c5307["tehran_local"].startswith("2016-01-01T00:05")
    assert c5307["offset_hours"] == 3.5
    assert c5307["file_timestamp"] == "01/01/2016, 00:05"
    assert c5307["intended_utc"].startswith("2015-12-31T20:35")

    # Tehran DST (Iran observed DST in 2016) -> +04:30, NOT hard-coded +03:30
    summer = by_src["2016-08-01T12:00:00+00:00"]
    assert summer["offset_hours"] == 4.5, summer
    assert summer["file_timestamp"] == "01/08/2016, 16:30"
    assert summer["intended_utc"].startswith("2016-08-01T12:00")

    # after DST ended -> +03:30
    winter = by_src["2016-11-01T12:00:00+00:00"]
    assert winter["offset_hours"] == 3.5, winter
    assert winter["intended_utc"].startswith("2016-11-01T12:00")

    # direct _date_str on the exact 5307 source instant
    assert _date_str("2015-12-31T20:35:57+00:00") == "01/01/2016, 00:05"
    assert tehran_local_of("2015-12-31T20:35:57+00:00").strftime("%H:%M") == "00:05"


def test_chat_txt_caption_stays_on_media_line(tmp_path):
    """Media + caption must be ONE _chat.txt line, not a detached caption."""
    a = Archive(tmp_path / "archive")
    a.create()
    a.write_manifest({"run_id": "x"})
    (a.media_dir / "1__0__photo.jpg").write_bytes(b"JPEGBYTES")
    rec = {
        "schema_version": 1, "source_message_id": 7,
        "date": "2015-12-31T20:35:57+00:00",
        "text": "مثل گیسویی که باد آنرا پریشان می کند",
        "caption": "مثل گیسویی که باد آنرا پریشان می کند",
        "media": [{"path": "media/1__0__photo.jpg", "sha256": "abc",
                   "filename": "photo.jpg", "type": "photo",
                   "constructor": "MessageMediaPhoto"}],
        "entities": [], "reply_to": None, "forward": None, "grouped_id": None,
        "from_id": {"user_id": 100}, "reactions": None,
    }
    a.append_canonical(rec)
    pkg_out = tmp_path / "package"
    build_import_package(a, pkg_out)
    lines = (pkg_out / "_chat.txt").read_text().strip().splitlines()
    assert len(lines) == 1, lines
    assert "<Attached: photo.jpg>" in lines[0]
    assert lines[0].endswith(": <Attached: photo.jpg> مثل گیسویی که باد آنرا پریشان می کند")
    assert "01/01/2016, 00:05" in lines[0]  # Tehran local, not 20:35