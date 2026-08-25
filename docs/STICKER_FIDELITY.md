# Sticker Fidelity

## Source-side (export) — complete identity preserved

A Telegram sticker is archived as its real document, NOT as `sticker_123.webp`:

- original file (bytes) + SHA-256
- document ID, access hash, file reference (scrubbed in raw snapshot)
- MIME (e.g. `image/webp`, `application/x-tgsticker`, `video/webm`)
- dimensions (sticker width/height)
- sticker emoji, sticker set / set ID
- animated (TGS) flag, video (WEBM) flag
- full `DocumentAttributeSticker` / `DocumentAttributeVideo` / filename attributes
- thumbnail

`STICKER_EXACT` identity → the source `DocumentAttributeSticker` + set.

## Import-side — what history import actually does

Official references: https://core.telegram.org/api/stickers, https://core.telegram.org/schema.

`messages.uploadImportedMedia` accepts an `InputMediaUploadedDocument` with
`DocumentAttributeFilename` (and optionally a video attribute). **It does NOT
carry `DocumentAttributeSticker` or an `InputStickerSet`.** Consequently an
imported sticker arrives in the target as a **generic `MessageMediaDocument`
with a filename attribute** — NOT as a Telegram sticker entity (no sticker-set,
no emoji).

Verified against actual target MTProto in `DOCUMENT_ONLY` classification in the
verification report.

## Classification

The verification engine inspects the **actual target constructor + document
attributes** (never just "has_media_object") and labels each source sticker:

- `STICKER_EXACT` — target is a document carrying `DocumentAttributeSticker`
  (sticker semantics intact). Not currently achievable via history import.
- `STICKER_SEMANTIC_PARTIAL` — target is a real `MessageMediaDocument` with the
  right bytes/MIME, but the sticker *entity* (set, emoji) is not attached.
- `STICKER_DOCUMENT_ONLY` — generic document, no sticker attributes at all.
- `FAILED` / `MEDIA_ABSENT` — no media object in the target.

A generic WebP with no sticker attributes is **`STICKER_DOCUMENT_ONLY`, never
`MEDIA_RESTORED`.**

## Post-import sticker reconstruction (investigation)

The spec asks whether a sticker could be reconstructed as
`Document + DocumentAttributeSticker + InputStickerSet` after import. Because a
sticker *entity* is only meaningful if the sticker belongs to a **preexisting,
official sticker pack** the target account has access to, and the source bytes
are files imported from an arbitrary archive, re-attaching the original pack
semantics is generally not possible without the exact pack + a fresh send.
Sending a media file with `InputMediaUploadedDocument(attributes=[Sticker]...)`
via a **new** `messages.sendMedia` would create a *current* message with the
file loosely labelled as a sticker — this is **not** a historical restore and is
labeled `RECONSTRUCTED_AFTER_IMPORT` at most, and only if attempted.

So stickers remain:
- archive: complete (set, emoji, file, attributes)
- import: `STICKER_DOCUMENT_ONLY` / `STICKER_SEMANTIC_PARTIAL`
- never falsely marked `STICKER_EXACT`.