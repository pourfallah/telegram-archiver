"""Media module: InputMedia construction for the import package, and the
attach-name scheme shared by serializer and uploader.

The serializer (importer.build_import_file) writes `<attached: NAME>` lines.
The uploader (ImportEngine.upload_media) uploads the same NAMEs. This module
owns the mapping rules so both sides never drift.
"""

from __future__ import annotations

from pathlib import Path

STICKER_MIME = "image/webp"
TGS_MIME = "application/x-tgsticker"


def ext_for_mime(mime: str, fallback: str = ".bin") -> str:
    known = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "video/mp4": ".mp4",
        "audio/mpeg": ".mp3",
        "audio/ogg": ".ogg",
        "application/pdf": ".pdf",
    }
    return known.get(mime, fallback)


def attach_name_for(media_id: str, mime: str | None, filename: str | None) -> str:
    """Unique per media record; shares no normalizable substring collisions.

    Telegram normalizes `{stem}__{id}{ext}` back to the base name, so we use
    the media_id (which already embeds the source message id) as the stem.
    """
    ext = Path(filename).suffix.lower() if filename and "." in filename else ext_for_mime(mime or "")
    return f"{media_id}{ext}"


def build_input_media(path: Path, mime: str, *, attach_name: str | None = None, media_type: str = "document", sticker: bool = False, animated: bool = False, file_handle=None):
    """Build the InputMedia for uploadImportedMedia.

    Sticker handling: DocumentAttributeSticker is attached ONLY for static
    image/webp stickers. Live-proven: `.tgs` (application/x-tgsticker) with a
    sticker attribute makes the target message materialize EMPTY, so tgs is
    imported as a plain document (DOCUMENT_ONLY, honest classification).
    image/gif (real .gif bytes) also materializes EMPTY via the animated path —
    import as plain document (DOCUMENT_ONLY) so the file is preserved.
    """
    from telethon import types

    handle = file_handle if file_handle is not None else path  # uploaded InputFile
    if mime.startswith("image/") and mime != "image/gif" and not path.name.endswith(".tgs"):
        from telethon import types as t

        return t.InputMediaUploadedPhoto(file=handle)
    attrs = [types.DocumentAttributeFilename(attach_name or path.name)]
    if path.name.endswith(".tgs") or mime == TGS_MIME or mime == "image/gif":
        # Animated .tgs stickers: bind an upload token but Telegram DROPS the
        # media at materialization (empty message) if uploaded with the native
        # x-tgsticker mime. Live-proven (old repo commit 6150515, re-verified
        # v2 run 121240: uploaded with x-tgsticker -> target EMPTY). Upload the
        # same bytes with a generic mime -> materializes as plain document,
        # file preserved (DOCUMENT_ONLY, honest). image/gif behaves the same.
        mime = "application/octet-stream"
    elif sticker:
        attrs.insert(0, types.DocumentAttributeSticker(alt="", stickerset=types.InputStickerSetEmpty()))
    if animated or mime == "image/gif":
        # real .gif bytes reached here only via `animated` flag; keep the attr
        attrs.insert(0, types.DocumentAttributeAnimated())
    if mime.startswith("video/"):
        attrs.append(types.DocumentAttributeVideo(0, 0, 0, supports_streaming=True))
    if mime.startswith("audio/"):
        attrs.append(types.DocumentAttributeAudio(0, voice=(media_type == "voice")))
    return types.InputMediaUploadedDocument(file=handle, mime_type=mime, attributes=attrs)
