"""Media routing + filename preservation unit tests."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from recovery.media import build_input_media  # noqa: E402


def test_webp_sticker_document_path():
    """image/webp must NOT go photo path (MessageMediaEmpty); sticker attr kept."""
    from telethon.tl.types import InputMediaUploadedPhoto, InputMediaUploadedDocument

    m = build_input_media(Path("sticker.webp"), "image/webp",
                          attach_name="m5632859.webp", media_type="sticker",
                          sticker=True)
    assert isinstance(m, InputMediaUploadedDocument), type(m)
    names = [type(a).__name__ for a in m.attributes]
    assert "DocumentAttributeSticker" in names
    assert "DocumentAttributeFilename" in names
    assert m.mime_type == "image/webp"


def test_webp_document_preserves_orig_filename():
    m = build_input_media(Path("sticker.webp"), "image/webp",
                          attach_name="m5632859.webp", media_type="sticker",
                          sticker=True, orig_filename="sticker.webp")
    fns = [a.file_name for a in m.attributes
           if type(a).__name__ == "DocumentAttributeFilename"]
    assert fns == ["sticker.webp"]


def test_mp3_preserves_orig_filename():
    m = build_input_media(Path("4693921_audio.mp3"), "audio/mpeg",
                          attach_name="m4693921.mp3", media_type="audio",
                          orig_filename="Mohsen-Chavoshi-Madar-320.mp3")
    fns = [a.file_name for a in m.attributes
           if type(a).__name__ == "DocumentAttributeFilename"]
    assert fns == ["Mohsen-Chavoshi-Madar-320.mp3"]
    # audio attr present, not voice
    auds = [a for a in m.attributes if type(a).__name__ == "DocumentAttributeAudio"]
    assert auds and not auds[0].voice


def test_photo_still_photo_path():
    m = build_input_media(Path("photo.jpg"), "image/jpeg",
                          attach_name="m4224458.jpg", media_type="photo")
    assert type(m).__name__ == "InputMediaUploadedPhoto"


def test_gif_uses_orig_filename():
    m = build_input_media(Path("1079283_gif.mp4"), "video/mp4",
                          attach_name="m1079283.mp4", media_type="gif",
                          animated=True, orig_filename="giphy.mp4")
    fns = [a.file_name for a in m.attributes
           if type(a).__name__ == "DocumentAttributeFilename"]
    assert fns == ["giphy.mp4"]
    names = [type(a).__name__ for a in m.attributes]
    assert "DocumentAttributeAnimated" in names