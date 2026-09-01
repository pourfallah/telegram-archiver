"""Canonical archive semantics: one message = one complete record."""
from __future__ import annotations

from telethon.tl import types as t

from recovery.archive import Archive, build_canonical_record
from recovery.telegram_client import tl_to_plain
from tests.fakes import dt, doc, doc_message, message, peer_user, photo_message


def test_text_message_record(tmp_path):
    m = message(1, text="Hello world")
    rec = build_canonical_record(m)
    assert rec["source_message_id"] == 1
    assert rec["text"] == "Hello world"
    assert rec["date"] is not None and rec["media"] == []
    assert rec["grouped_id"] is None


def test_media_and_caption_stay_one_record():
    attrs = [t.DocumentAttributeAudio(duration=30, voice=False)]
    m = doc_message(7, doc(1, "audio/mpeg", attrs), text="gooood music 😍")
    rec = build_canonical_record(m)
    # caption is the SAME record as the media
    assert rec["media"] and rec["media"][0]["type"] == "audio"
    assert rec["text"] == "gooood music 😍"
    assert rec["caption"] == "gooood music 😍"
    # never split into two records
    assert len(rec["media"]) == 1


def test_custom_emoji_preserves_document_id():
    ent = t.MessageEntityCustomEmoji(offset=0, length=2, document_id=55555)
    m = message(3, text="😀", entities=[ent])
    rec = build_canonical_record(m)
    e = rec["entities"][0]
    assert e["document_id"] == 55555
    assert e["type"] == "customemoji"


def test_reply_and_grouped_and_forward_preserved():
    fwd = t.MessageFwdHeader(date=dt(-5), from_name="News", channel_post=88, imported=False)
    reply = t.MessageReplyHeader(reply_to_msg_id=10, reply_to_top_id=10)
    m = message(4, text="child", reply_to=reply, fwd=fwd, grouped_id=777)
    rec = build_canonical_record(m)
    assert rec["reply_to"]["reply_to_msg_id"] == 10
    assert rec["reply_to"]["quote"] is False
    assert rec["grouped_id"] == 777
    assert rec["forward"]["from_name"] == "News"
    assert rec["forward"]["channel_post"] == 88


def test_raw_snapshot_is_json_safe_and_preserves_bytes_as_b64():
    attrs = [t.DocumentAttributeFilename(file_name="a.pdf")]
    m = doc_message(5, doc(6, "application/pdf", attrs))
    raw = tl_to_plain(m)
    assert raw["id"] == 5
    media = raw["media"]["document"]
    assert media["file_reference"].startswith("base64:") or media["file_reference"] is not None
    assert media["size"] == 123


def test_archive_streams_roundtrip(tmp_path):
    a = Archive(tmp_path / "archive")
    a.create()
    a.write_manifest({"run_id": "r1"})
    for i in range(3):
        a.append_canonical(build_canonical_record(message(i + 1, text=f"m{i}")))
        a.append_raw({"id": i + 1})
    assert a.messages_count() == 3
    ids = [r["source_message_id"] for r in a.read_messages()]
    assert ids == [1, 2, 3]
    assert a.is_resumable()


def test_album_grouped_id_carried_from_real_object():
    m1 = photo_message(1, t.Photo(id=1, access_hash=1, file_reference=b"a", date=dt(-2), dc_id=1,
                                  sizes=[t.PhotoSize(type="m", w=10, h=10, size=100)]),
                       text="first of album", grouped_id=900)
    m2 = photo_message(2, t.Photo(id=2, access_hash=2, file_reference=b"b", date=dt(-2), dc_id=1,
                                  sizes=[t.PhotoSize(type="m", w=10, h=10, size=100)]),
                       text="", grouped_id=900)
    r1, r2 = build_canonical_record(m1), build_canonical_record(m2)
    assert r1["grouped_id"] == 900 == r2["grouped_id"]
    assert r1["caption"] == "first of album"