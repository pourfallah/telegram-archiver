# Telegram Import Media — payload audit & single-media probe

Companion to `docs/TELEGRAM_IMPORT_PROTOCOL.md`. This file pins down the exact
`media` payloads for `messages.uploadImportedMedia`, what determines whether a
media file attaches, and how the controlled probe discriminates the failure modes.

Sources: official TL schema (layer 223, via installed Telethon binding), the
official `messages.uploadImportedMedia` method page, `/api/import`, and the
measured live results of the previous real E2E.

---

## 1. `uploadImportedMedia` signature (layer 223)

```
messages.uploadImportedMedia #2a862092
  peer:      InputPeer
  import_id: long          # from messages.HistoryImport.id (initHistoryImport)
  file_name: string        # binds this upload to an <Attached: …> marker
  media:     InputMedia
  = MessageMedia
```

The return is a `MessageMedia`. For a correctly attached photo it should be a
`MessageMediaPhoto` (a **non-empty** `MessageMedia`); for a correctly attached
document it should be `MessageMediaDocument`. If the server returns
`MessageMediaEmpty`, the media did not get associated.

### Upload file object: `InputFile` vs `InputFileBig`
```
inputFile #f52ff27f   id: long   parts: int   name: string   md5_checksum: string
inputFileBig #fa4f0bb5 id: long   parts: int   name: string
```
- Files ≤ 10 MB are uploaded with `upload.saveFilePart` -> `InputFile`
  (`parts`, `md5_checksum`).
- Files > 10 MB use `upload.saveBigFilePart` -> `InputFileBig` (`parts`, no md5).
- Telethon's `client.upload_file(bytes, file_name=…)` produces exactly the correct
  one. This was already being used correctly.

### Per-media-class `InputMedia` (schema field sets)
| Type | `media` object | Key fields |
|---|---|---|
| photo | `inputMediaUploadedPhoto` | `file: InputFile` |
| video | `inputMediaUploadedDocument` | `file`, `mime_type`, `attributes=[DocumentAttributeVideo]` |
| GIF/animation | `inputMediaUploadedDocument` | `file`, `mime_type=image/gif` or video, `attributes=[DocumentAttributeAnimated]` |
| audio | `inputMediaUploadedDocument` | `file`, `mime_type=audio/*`, `attributes=[DocumentAttributeAudio(voice=false, performer, title)]` |
| voice | `inputMediaUploadedDocument` | `file`, `mime_type=audio/ogg`, `attributes=[DocumentAttributeAudio(voice=true)]` |
| document | `inputMediaUploadedDocument` | `file`, `mime_type`, `attributes=[DocumentAttributeFilename]` |
| sticker | `inputMediaUploadedDocument` | `file`, `mime_type`, `attributes=[DocumentAttributeSticker]` (only if source has sticker attributes) |

Note (`inputMediaUploadedDocument` full set, layer 223):
`file, mime_type, attributes, nosound_video, force_file, spoiler, thumb, stickers,
video_cover, video_timestamp, ttl_seconds`.

---

## 2. The filename association mechanism

`uploadImportedMedia(file_name=…)` is how the server links a media upload to a
specific message line in the export file. The server binds the upload to the
message whose attachment marker contains that exact `file_name`.

Consequences (what we must guarantee, and the probe will assert):
- The export-file marker text MUST use the exact spelling Telegram's parser
  recognizes (the probe's `checkHistoryImport` + a real single-message file
  establishes this; we record whatever marker syntax is accepted).
- `file_name` passed to `uploadImportedMedia` MUST byte-equal the filename inside
  that accepted marker.
- No path prefix is carried in the schema's `file_name` (it is a plain name), so a
  marker like `<Attached: subdir/IMG.jpg>` or a renamed file will fail to bind.
- `media_count` passed to `initHistoryImport` MUST equal the number of
  `<Attached: …>` markers actually present in the file, and every one of those
  markers MUST be backed by a successful upload. A declared-but-unuploaded media
  count appears to make the server treat the import as inconsistent (see §4).

---

## 3. Ordering requirements

- `uploadImportedMedia` for **every** media file must complete BEFORE
  `startHistoryImport` (official: "To be called only after … uploading all files
  using messages.uploadImportedMedia").
- Ordering ACROSS different files is not constrained by the API; the binding is by
  `file_name`, not by sequence. We still upload in file order for determinism.
- Do not reuse/retry an `import_id` that already had uploads; a fresh
  `initHistoryImport` yields a fresh id.

---

## 4. Evidence from the failed real E2E (measured, not assumed)

- All five RPCs returned success; `media_count=101`; 101 `uploadImportedMedia`
  calls each returned truthy; `startHistoryImport` truthy.
- Immediate read-back: **every** imported target message had `media=None`. So
  `uploadImportedMedia` returning a truthy/non-empty result at the RPC level did
  NOT produce attached media on any message.
- The whole import was later **rolled back server-side** (150 -> 1, both A and B
  read only 1 non-imported message). A media-declared-but-empty import is not
  durable.

This strongly implies the failure is in the **media payload / filename / marker**
layer — the server accepted the flow but could not bind any media, so it imported
text-only and then reverted. The single-media probe isolates exactly which of the
candidate causes is real.

---

## 5. Discrimination plan — the single-media probe (source 5307)

One source message: `id 5307`, photo + caption,
`2015-12-31T20:35:57Z` (Tehran `2016-01-01 00:05 +03:30`).

1. Fetch 5307 from A (read-only), download the photo, record SHA-256, media
   constructor, original filename.
2. Build a ONE-message export file (private-chat line) with one `<Attached: …>`
   marker + caption on the same line, Tehran-local naive timestamp.
3. `checkHistoryImport(first ≤100 lines)` -> assert `HistoryImportParsed.pm == true`.
4. `checkHistoryImportPeer(target A<->B)` -> assert success.
5. `initHistoryImport(peer, file, media_count=1)` -> log `HistoryImport.id`.
6. `uploadImportedMedia(peer, import_id, file_name=exactMarkerName,
   media=InputMediaUploadedPhoto(file=uploaded))`; log the **serialized InputMedia**,
   the exact `file_name`, and the **returned MessageMedia**.
7. Assert the returned `MessageMedia` is NOT `MessageMediaEmpty`. If it is, STOP
   and record the InputMedia + filename used (this alone answers the "why").
8. `startHistoryImport(peer, import_id)`.
9. Read target at T0/T1/T2/T3; record: message count, ids, `target.media`
   constructor, `target.message` (caption equality), `target.fwd_from`
   (`imported/date/from_id/from_name`), `target.date`.
10. Discovery gates: never touch A<->C; clear B only via the safe
    `just_clear=true, revoke=false` if cleanup is required.

### What each outcome proves
- Returned `MessageMediaEmpty` + InputMedia logged -> **hypothesis 1** (payload
  wrong / server rejects it as unboundable).
- Non-empty `MessageMedia` at upload but `target.media == None` -> binds at RPC but
  **not to the message** -> **hypothesis 2/3** (filename/marker mismatch).
- Non-empty return AND `target.media` photo attached -> the earlier 150-message
  failure was a filename/marker/ordering bug we can then replicate correctly.
- Import rolls back (count -> 0 at T1/T2/T3) -> **server-side rollback** present
  even for a single correct media import -> a Telegram server/account limitation.

---

## 6. Correctness constraints (no inventing)
- Never set `target.from_id` ourselves; only carry what the API stores
  (`fwd_from.from_name` at most). We stop using the `user_<id>` placeholder unless
  the probe shows the server keeps exactly it.
- Never "correct" `message.date` after import. Report exact vs minute-exact vs the
  +1 h DST decode error.
- Never claim media restored from a local file or from an RPC returning truthy —
  only from a live `target.media` read OR the upload's `MessageMedia` signal plus a
  durable live read.

---

## 7. MEASURED ROOT CAUSE OF THE MEDIA FAILURE (2026-09-02, controlled probe)

Evidence is from the isolated single-message probe (source 5307, photo+caption,
`--variant photo`), a real import into A<->B, read back live.

### 7.1 The probe flow all succeeded
- `checkHistoryImport` -> no error (`pm=False, group=False, title=None`).
- `checkHistoryImportPeer` -> `CheckedHistoryImportPeer` (target eligible).
- `initHistoryImport(media_count=1)` -> `import_id`.
- `uploadImportedMedia(file_name='probe_5307.jpg',
  media=InputMediaUploadedPhoto(file=InputFile(id=…, parts=1, name='probe_5307.jpg',
  md5_checksum='247e…')))` -> **`MessageMediaPhoto`** (NON-empty).
- `startHistoryImport` -> `True`.

Durability (full T0/T1/T2/T3, same run): `T0: 1 total, 0 media` -> `T1/T2/T3:
2 total, 0 media (imported=1)`. The imported message **persisted** (no
`SERVER_SIDE_ROLLBACK`) but **never gained media** at any sample point.

### 7.2 The imported target message (raw, live)
```
id          = 5945
date        = 2015-12-31 20:35:00+00:00          # minute-exact from 20:35:57Z ✓
message     = "<Attached: probe_5307.jpg> مثل گیسویی که باد آنرا پریشان می کند …"
              # ^ the ENTIRE LINE became plain message text; the marker was NOT
              #   parsed — it appears verbatim in target.message.
media       = None
fwd_imported= true
fwd_date    = 2015-12-31 20:35:00+00:00          # minute-exact ✓
fwd_from_id = None
fwd_from_name= "Probe"                           # = the line's sender display name
```

### 7.3 Conclusion (evidence-based, not assumed)
**The server never recognized any media marker we tried as an attachment
marker.** It imported the colon-plus-message part of each line verbatim, so the
uploaded media had *no message line to bind to*, and `target.media` stayed
`None`. `uploadImportedMedia` returned a non-empty `MessageMediaPhoto` only
because the **upload itself** was valid — the returned media is the server's
echo of the upload, NOT a binding to any imported message.
This is exactly why the earlier 101-media import showed zero attached media and
then rolled back: the file's media marker was never parsed.

Positive confirmations from the same probe:
- Tehran timestamp encoding is correct: source `2015-12-31T20:35:57Z` became
  `message.date == fwd_date == 2015-12-31T20:35:00+00:00` (minute-exact, no −3:30 shift).
- `fwd_from.imported=true` is set by the server.
- `fwd_from.from_name` is filled from the line's sender name — that is the only
  sender-metadata mechanism (a display string, never a peer id).

### 7.4 Every marker syntax we tried fails; the file is never recognized
Four controlled single-photo-import trials, identical flow (upload chat file,
`initHistoryImport(media_count=1)`, one `uploadImportedMedia`, `startHistoryImport`),
all leading to a durable text-only message with the marker verbatim in
`target.message` and `target.media == None`:
1. `<Attached: fname> caption` on one line
2. `&lt;attached: fname&gt; caption` (HTML-escaped)
3. `<attached: fname> caption` with iOS bracket+seconds format
4. `<attached: fname>` marker-only (exact iOS line, no caption)

In every trial `uploadImportedMedia` returned a non-empty `MessageMediaPhoto`
and `startHistoryImport` returned `True`, yet `target.media` stayed `None` and
the marker text was imported. `checkHistoryImport` also returned
`pm=False, group=False, title=None` (unknown type) for **every** file structure
tried — the server has never classified any of our export files as a supported
private-chat export.

### 7.5 The WhatsApp E2E "magic header" does not flip recognition
Per the WhatsApp spec (Option 1 Android), the file was built with the exact E2E
system message as line 1:
```
01/01/2016, 00:05 - Messages and calls are end-to-end encrypted. No one outside of this chat, not even WhatsApp, can read or listen to them.
01/01/2016, 00:05 - First: probe_5307.jpg (file attached)
01/01/2016, 00:05 - First: <caption>
```
`checkHistoryImport` still returned `pm=False, group=False, title=None` (halted —
no import ran, per the gate). A broader read-only matrix (Android header, iOS
header, header-only, header + 2 participants, long 2-participant file, header
without trailing period, different header date) **all** returned unrecognized.
The server never classifies any synthetic file as a supported export, so media
binding (which appears gated on that recognition) is unattainable from synthetic
files. Only a **genuine real WhatsApp export sample** can confirm the exact
recognized byte/line structure; until then media import remains UNSUPPORTED (in
practice) for this configuration. No multi-message E2E may run until a single
photo attaches AND is durable (T0/T1/T2/T3).
### 7.6 RESOLVED — the working format (extracted from the prior working engine)

Root cause of the four "media never attaches" trials above: the export file was a
**single media line**. Telegram's parser only classifies the file as a chat and
binds media when it looks like a realistic **multi-line private chat with the
media line in the middle**. The prior (working) engine's proven reproducer used
exactly this shape:

```
[05/01/2024, 10:00:00] John Doe: Hello
[05/01/2024, 10:00:30] Jane Smith: <attached: 00000042-PHOTO-2024-01-05-10-00-30.jpg>
[05/01/2024, 10:01:00] John Doe: After photo
```

Verified live with source 5307 (photo+caption -> A<->B), target read back:
- Timestamp: `[DD/MM/YYYY, HH:MM:SS]` (brackets, day-first, 24-hour, WITH seconds),
  written at the Asia/Tehran wall-clock -> the exact source UTC instant is
  preserved (`target.message.date == target.fwd.date == source`).
- Media marker: `filename.ext (file attached)` (Android) or `<attached: filename>`,
  filename MUST equal the `file_name` passed to `uploadImportedMedia`.
- File shape: a leading text line, the media line in the MIDDLE, a trailing
  line; two distinct sender names. A single line is NOT recognized.
- photo: `InputMediaUploadedPhoto(file)` -> target `MessageMediaPhoto`.
- `pm` may be `false`; media still attaches (no pm gate required).
- Imported photo: `media=MessageMediaPhoto`, `fwd.imported=true`,
  `fwd.from_name=<line sender>`, exact date. Caption becomes a SEPARATE text
  message (the photo `message` is empty) — matches the prior engine's matrix
  ("photo restored + separate caption msg"); caption-on-same-message is not
  produced by Telegram's import.
- Durable: T0/T1/T2/T3 keep the photo (`with_media>=1`), no rollback.

### 7.7 Document/audio/video bind via `<attached: fname>` — ranges tested
Follow-up typed probes (A<->C source -> A<->B), same 3-line shape + bracket+seconds:
- **Photo** `InputMediaUploadedPhoto` -> target `MessageMediaPhoto` (durable, exact date).
- **Video** `InputMediaUploadedDocument(+DocumentAttributeVideo,DocumentAttributeFilename)` ->
  `MessageMediaDocument` durable.
- **Audio** `InputMediaUploadedDocument(...)` with **`InputFileBig`** (103 parts, no md5)
  -> `MessageMediaDocument` durable, date preserved.
- **Animated sticker `.tgs`** (`DocumentAttributeSticker` + `InputStickerSetID`) ->
  does NOT bind (imported as literal text). Treat Telegram-native animated stickers
  as UNSUPPORTED_BY_TELEGRAM via foreign import (matching the old matrix's
  "sticker ⚠️ check attrs / pending" caveat); a static WEBP sticker may differ and
  is untested.

Marker rule (measured): the **`<attached: filename>`** token binds BOTH photos and
documents. The Android **`filename (file attached)`** form binds photos but NOT
documents (documents imported as text with that token). Use `<attached: …>`.

OPEN (date): import-time fallback observed — some imports preserved the exact
historical `message.date`/`fwd.date` (photo 2015, audio 2026-08-05) while others
(video batch) landed at `message.date = import time`. The file timestamp is not
always applied. Needs investigation (likely tied to rapid/concurrent imports or a
server race). A large `messages.initHistoryImport` flood (~74k s) was hit after
~12 imports in ~2 h, blocking further live probing.

## §7.8 Captions / albums / replies — measured limits (2026-09-03)

Tested live (single initHistoryImport each, A<->C->A<->B read-back):

1. **Bare caption line (no `[ts] Sender:` header) BREAKS media binding.** A line
   `caption text` directly under `<attached: cap.png>` makes the parser stop
   recognizing the file as a chat: the media marker merges into one TEXT message
   `<attached: cap.png>\ncaption text` and the photo is LOST (no MessageMediaPhoto).
   => Every content line MUST be `[DD/MM/YYYY, HH:MM:SS] Sender: ...`. The only
   conformant caption handling is `[ts] Sender: caption` as a trailing line, which
   Telegram imports as a SEPARATE text message (there is NO caption-on-media).

2. **Albums do NOT group via shared timestamp.** Two videos with the IDs that the
   engine forces to the identical `when` imported as two SEPARATE messages
   (`grouped_id=None`), not an album. Telegram's import parser does not create
   media groups from same-timestamp consecutive media.

3. **Replies flatten.** Messages carrying `reply_to_id` import as plain text
   messages with `reply_to=None` — no reply/quote threading is preserved. There is
   no quote syntax Telegram's import parser renders into a reply.

4. **Why:** the official protocol (`core.telegram.org/api/import`, 5 RPCs) has NO
   caption/album/reply mechanism; the parser only decodes WhatsApp-format text
   lines. `inputSingleMedia` (a possible source of the expectation) is defined
   ONLY for `messages.sendMultiMedia` — the SEND path, which is forbidden here as
   history recovery. These three behaviors are Telegram Import API limitations.

Implication for `full_migration_engine.py`: keep captions as `[ts] Sender: caption`
trailing lines (separate text message); keep album members at identical timestamps
(no-op, no grouping); replies import flat. Do NOT emit bare lines after markers.
