"""Media classification driven by real Telethon document attributes."""
from __future__ import annotations

from telethon.tl import types as t

from recovery.media import classify_media, safe_filename
from tests.fakes import doc, doc_message, photo_message, message


def _photo():
    return t.Photo(id=3, access_hash=4, file_reference=b"abc", date=None, dc_id=2,
                   sizes=[t.PhotoSize(type="m", w=640, h=480, size=5000)])


def test_photo_classified_faithfully():
    recs = classify_media(photo_message(1, _photo()))
    assert len(recs) == 1
    m = recs[0]
    assert m["type"] == "photo" and m["constructor"] == "MessageMediaPhoto"
    assert m["media_id"] == 3 and m["access_hash"] == 4
    assert m["sizes"][0]["width"] == 640


def test_sticker_not_just_webp():
    attrs = [t.DocumentAttributeSticker(alt="hi", stickerset=t.InputStickerSetID(id=5, access_hash=7))]
    d = doc(9, "image/webp", attrs)
    m = classify_media(doc_message(2, d))[0]
    assert m["type"] == "sticker"
    assert m["sticker"]["alt"] == "hi"
    assert m["sticker"]["stickerset"]["__tl__"] == "InputStickerSetID"


def test_audio_vs_voice_vs_video():
    aud = doc(1, "audio/mpeg", [t.DocumentAttributeAudio(duration=30, voice=False, title="T", performer="P")])
    assert classify_media(doc_message(1, aud))[0]["type"] == "audio"
    voic = doc(2, "audio/ogg", [t.DocumentAttributeAudio(duration=4, voice=True)])
    assert classify_media(doc_message(2, voic))[0]["type"] == "voice"
    vid = doc(3, "video/mp4", [t.DocumentAttributeVideo(duration=5, w=16, h=9, supports_streaming=True)])
    v = classify_media(doc_message(3, vid))[0]
    assert v["type"] == "video" and v["width"] == 16 and v["duration"] == 5


def test_gif_uses_actual_attributes():
    # animated file WITHOUT DocumentAttributeAnimated must NOT be called gif
    plain = doc(4, "video/mp4", [t.DocumentAttributeVideo(duration=2, w=8, h=8)])
    assert classify_media(doc_message(4, plain))[0]["type"] == "video"
    anim = doc(5, "video/mp4", [t.DocumentAttributeAnimated()])
    assert classify_media(doc_message(5, anim))[0]["type"] == "animation"


def test_plain_document():
    d = doc(6, "application/pdf", [t.DocumentAttributeFilename(file_name="report.pdf")])
    m = classify_media(doc_message(6, d))[0]
    assert m["type"] == "document" and m["filename"] == "report.pdf"


def test_safe_filename_sanitizes_and_deduplicates_style():
    assert safe_filename("a/b\\c:d?.pdf", "f") == "a_b_c_d_.pdf"
    assert safe_filename("..", "f") == "f"