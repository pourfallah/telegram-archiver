# Media Fidelity

## Source-side — rich, per-type descriptors

The canonical archive never collapses media into a generic file. Each object
carries type-specific metadata:

- **photo**: type, size, original filename
- **video**: type, MIME, size, duration, width, height, round flag, filename
- **animation/GIF**: type, MIME, `gif: true`, size, filename
- **document**: type, MIME, size, original filename
- **voice**: type, MIME, duration, `voice: true`, title, performer
- **audio**: type, MIME, duration, title, performer, filename
- **sticker**: type, MIME, size, sticker emoji, animated flag, filename
- **contact**: type, `{first_name, last_name, phone}`
- **geo**: type (additional attrs serialized where present)
- **unknown**: preserved (never silently dropped)

## Import-side — what is verifiably attached

Through the fixed pipeline (`ImportMediaUploadedDocument`/`Photo` + filename
association) the following land as REAL `MessageMedia*` objects in the target
(verified by importing the actual target MTProto object):

- photo → `MessageMediaPhoto`  PHOTO_EXACT
- video → `MessageMediaDocument[DocumentAttributeVideo]`  VIDEO_EXACT
- GIF/animation → `MessageMediaDocument[DocumentAttributeAnimated]`  ANIMATION_EXACT
- document → `MessageMediaDocument[DocumentAttributeFilename]`  DOCUMENT_EXACT
- voice → `MessageMediaDocument[DocumentAttributeAudio].voice`  VOICE_EXACT (if MIME + voice attr survive)
- audio → `MessageMediaDocument[DocumentAttributeAudio]`  AUDIO_EXACT
- sticker → `MessageMediaDocument[DocumentAttributeFilename]`  **STICKER_DOCUMENT_ONLY** (no `Sticker` attr — see STICKER_FIDELITY.md)
- contact/geo/poll/other → **ARCHIVAL_ONLY** (not representable via history import)

The verification engine classifies by the **actual target constructor + document
attribute names** and never reports a success from "has_media_object" alone.

## Caption

A source `media + caption` is archived as ONE logical message (media with
`caption` + `caption_entities`). During import the `uploadImportedMedia` binds
the file, and the caption text lands as an adjacent separate text message
(matching Telegram's import behavior). Verification labels this:
- `CAPTION_ATTACHED` — the same target message carries media + caption (ideal)
- `CAPTION_SEPARATE` — caption is a distinct adjacent target message (current behavior)
- `CAPTION_LOST` — caption missing

`CAPTION_SEPARATE` is honest and expected; it is never called `CAPTION_ATTACHED`.

## Albums / grouped media

`grouped_id` is preserved in the archive so all album members stay associated.
Experiments are required before claiming albums re-materialize as one album in
the target; classification is `EXPERIMENTAL` until a live import of a multi-item
album verifies it.