# Media

## Classification (driven by actual Telethon attributes, not extensions)

Every source media becomes a first-class archive object via `classify_media`:

| Archive type | Decided by |
|---|---|
| `photo` | `MessageMediaPhoto` (+ all `PhotoSize`s, access_hash, file_reference) |
| `video` | `MessageMediaDocument` + `DocumentAttributeVideo` |
| `animation` | `DocumentAttributeAnimated` (NOT blindly "gif") |
| `audio` | `DocumentAttributeAudio` with `voice=False` (+ title/performer/duration) |
| `voice` | `DocumentAttributeAudio` with `voice=True` (+ waveform) |
| `sticker` | `DocumentAttributeSticker` (alt, stickerset, mask) |
| `document` | any other file (incl. `DocumentAttributeFilename`) |

A plain `WEBP` with only a filename attribute is a **document**, not a sticker —
the verifier labels it `DOCUMENT_ONLY`, never `STICKER_EXACT`.

The archive stores: media_id, source_message_id, type, constructor,
access_hash, file_reference (base64), mime, size, filename, width/height/
duration, all document attributes, thumbnail, spoiler/round/voice flags,
SHA-256, and relative `path`.

## Downloading

`MediaDownloader` streams `iter_download` → SHA-256 while writing → optional
resume checkpoint (`{sha256,size}`), so interrupted runs never re-download
files whose size+hash already match.

## Real fixtures (actual files, never just filenames)

`scripts/generate_media_fixtures.py` writes genuine files to
`scripts/fixtures/media/`:

| File | Container validity | Note |
|---|---|---|
| `photo.jpg` / `photo-caption.jpg` | valid JPEG | |
| `photo.png` | valid PNG | album item |
| `animation.gif` | valid animated GIF | |
| `sticker.webp` | valid WebP **file** | uploaded as a *document*, NOT a real sticker |
| `audio.mp3` | valid MPEG Layer III | |
| `voice.mp3` | valid MPEG Layer III | |
| `document.pdf` | valid PDF | |
| `video.mp4` | minimal MP4 **container** | not natively playable |
| `voice.ogg` | minimal Ogg container | not natively playable |

**Honest gaps.** A *true* Telegram sticker requires a real sticker-pack
`DocumentAttributeSticker`; the automated builder sends the WebP as a document,
so the sticker capability must be exercised by forwarding/encoding a genuine
sticker in the live E2E. Likewise playable `video.mp4` and voice `voice.ogg`
should be sent from the real app during the live E2E. These are recorded as
`NOT_AVAILABLE` if not produced — never silently skipped (rule #58).