"""End-to-end hermetic recovery flow: export -> package -> clear -> import ->
map -> verify, all offline over fake MTProto clients."""
from __future__ import annotations

from telethon.tl import types as t

from recovery.config import RecoveryConfig
from recovery.engine import TelegramRecoveryEngine
from tests.fakes import FakeClient, FakeRecoveryClient, dt, doc, doc_message, message, photo_message


def _source_history():
    photo = t.Photo(id=1, access_hash=10, file_reference=b"p", date=dt(-1), dc_id=2,
                    sizes=[t.PhotoSize(type="m", w=640, h=480, size=2000)])
    auth = doc(2, "audio/mpeg", [t.DocumentAttributeAudio(duration=30, voice=False,
                                                          title="Song", performer="K")])
    sticker = doc(3, "image/webp",
                  [t.DocumentAttributeSticker(alt="hi", stickerset=t.InputStickerSetID(id=5, access_hash=7))])
    return [
        photo_message(1, photo, text="RECOVERY_V2_PHOTO_CAPTION", date=dt(-1), from_uid=100),
        doc_message(2, auth, text="gooood music 😍", date=dt(-0.9), from_uid=100),
        doc_message(3, sticker, text="", date=dt(-0.8), from_uid=100),
        message(4, text="plain text", date=dt(-0.7), from_uid=100),
        message(5, text="plain text 2", date=dt(-0.6), from_uid=100),
    ]


def _build_engine(run_dir, src_history, tgt_history=None, download_media=True):
    cfg = RecoveryConfig(api_id_a=1, api_hash_a="a", api_id_b=2, api_hash_b="b",
                         run_dir=run_dir, download_media=download_media)
    src = FakeRecoveryClient(FakeClient(history=src_history), my_id=100)
    tgt = FakeRecoveryClient(FakeClient(history=tgt_history or []), my_id=200)
    e = TelegramRecoveryEngine(src, tgt, cfg)
    e.peer = "peer"
    return e, src, tgt


def test_full_offline_recovery_pipeline(tmp_path):
    src_history = _source_history()
    e, src, tgt = _build_engine(tmp_path, src_history)
    r = e.run

    # 1 export
    stats = await_(e.export())
    assert stats["messages"] == 5
    assert stats["media_downloaded"] == 3   # photo, audio, sticker

    # 2 verify archive
    v = await_(e.verify_export())
    assert v["ok"] is True and v["raw_complete"] is True

    # 3 build package
    p = await_(e.build_package())
    assert p["media"] == 3
    chat = (r.package_dir / "_chat.txt").read_text(encoding="utf-8")
    assert "RECOVERY_V2_PHOTO_CAPTION" in chat          # caption in same line block
    assert "<Attached:" in chat

    # 4 snapshot before + clear (target is empty here)
    await_(e.snapshot_target("before"))
    await_(e.clear_target())
    assert await_(e.verify_source_still_has_history(want=5)) is True

    # 5 import (fake target records RPCs but does not create messages)
    await_(e.import_package())
    assert "StartHistoryImportRequest" in tgt.client.calls
    # simulate that the import actually populated B's history
    tgt.client.set_history(src_history)

    # 6 snapshot after
    await_(e.snapshot_target("after"))

    # 7 map
    mapping = await_(e.map_source_to_target())
    assert len(mapping) == 5
    assert all(m.target_message_id >= 0 for m in mapping)

    # 8 verify -> report on disk
    report = await_(e.verify(mapping))
    assert report["summary"]["text"]["exact_pct"] == 100.0
    assert (r.root / "FINAL_REPORT.json").exists()
    assert (r.root / "FINAL_REPORT.html").exists()
    assert r.source_to_target.exists()
    assert r.media_trace.exists()

    # 9 per-field: photo caption attached; sticker (source id 3 -> row index 2)
    row0 = report["rows"][0]
    assert row0["caption"]["class"] == "CAPTION_ATTACHED"
    assert row0["photo"]["class"] == "EXACT"
    assert report["rows"][2]["sticker"]["class"] == "EXACT"
    assert report["rows"][1]["audio"]["class"] == "EXACT"


def await_(coro):
    import asyncio
    return asyncio.run(coro)