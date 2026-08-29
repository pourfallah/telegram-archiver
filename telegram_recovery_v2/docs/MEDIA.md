# Media — v2 engine

## Archiving (lossless)

Every source media is a first-class record (`archive/media/media_index.json`)
plus original bytes (`archive/media/files/`, SHA256 recorded in the index).
Records keep constructor, type classification, document_id/access_hash/
file_reference, dimensions, duration, MIME, filename, ALL document
attributes verbatim (sticker set, audio title/performer/waveform, video
flags), grouped_id, and the caption of their message.

Classification inspects actual attributes — never guesses from extension:
- DocumentAttributeSticker -> sticker
- DocumentAttributeAnimated -> gif
- DocumentAttributeAudio (voice) -> voice; else audio
- DocumentAttributeVideo -> video
- MessageMediaPhoto -> photo

## Import

The official import API accepts media as upload tokens bound by exact
filename to `<attached: NAME>` lines. v2 rules:

- attach name = `m{source_message_id}{ext}` — unique per media record, so
  duplicate filenames can never collide.
- MIME passed through from the archive (fallback application/octet-stream).
- Photos -> InputMediaUploadedPhoto; everything else ->
  InputMediaUploadedDocument with DocumentAttributeFilename.
- `.tgs` animated stickers are imported as PLAIN documents. Live-proven:
  attaching DocumentAttributeSticker to `application/x-tgsticker` makes the
  whole target message materialize EMPTY. Static webp stickers keep the
  sticker attribute.
- `uploadImportedMedia` returning `MessageMediaEmpty` is NOT diagnostic;
  the trace records the constructor but success is decided ONLY by reading
  the materialized target message.
