# Telegram Import Protocol — authoritative audit (Layer 223)

Date of audit: 2026-09-02.
Sources consulted (primary only): core.telegram.org `/api/import`, the five official
method pages for `messages.*` import RPCs, Telegram's official TL schema (via the
installed Telethon-generated schema, which is a faithful binding of layer
`schema.tl`), and the Telegram Desktop source tree
(`github.com/telegramdesktop/tdesktop`, `dev` branch).

Purpose: stop guessing. Reproduce, as far as Telegram's official import API
allows, the A<->C -> A<->B recovery using exactly what Telegram documents and
what the server measurably does. This is the single source of truth for the
import protocol. The previous `recovery_v2` importer failed a real E2E (media not
attached, whole import later rolled back server-side); this doc explains why and
what we now believe, and the next doc (`TELEGRAM_IMPORT_MEDIA.md`) details the
media layer.

---

## 1. The five RPCs and their exact signatures (layer 223, official)

All five exist in Telethon 1.44.0 (layer 223). Signatures below are from the
installed schema, cross-checked against the official method pages.

| Step | Method | Request fields | Returns |
|---|---|---|---|
| 1 | `messages.checkHistoryImport` | `import_head: string` | `messages.HistoryImportParsed` |
| 1b | `messages.checkHistoryImportPeer` | `peer: InputPeer` | `messages.CheckedHistoryImportPeer` |
| 2 | `messages.initHistoryImport` | `peer: InputPeer, file: InputFile, media_count: int` | `messages.HistoryImport { id: long }` |
| 3 | `messages.uploadImportedMedia` | `peer: InputPeer, import_id: long, file_name: string, media: InputMedia` | `MessageMedia` |
| 4 | `messages.startHistoryImport` | `peer: InputPeer, import_id: long` | `Bool` |

`messages.HistoryImportParsed` (the `checkHistoryImport` result):
```
messages.historyImportParsed #5e0fb7b9
  flags: #
  pm:    flags .0? true     # file came from a PRIVATE chat
  group: flags .1? true     # file came from a GROUP chat
  title: flags .2? string   # exported chat title
```
Only ONE of `pm`/`group` may be set; if neither, the chat type is unknown.

`messages.CheckedHistoryImportPeer` contains only `confirm_text: string`.

Official descriptions (layer 223 method pages):
- `checkHistoryImport` — "Obtains information about a chat export file, generated
  by a foreign chat app." Pass **up to 100 lines from the beginning of the file**.
- `checkHistoryImportPeer` — determine whether history may be imported into a
  specific peer; "typically allowed for private chats with a mutual contact or
  supergroups with change_info admin rights."
- `initHistoryImport` — "Import chat history from a foreign chat app into a
  specific Telegram chat"; pass the export file and `media_count`.
- `uploadImportedMedia` — "Upload a media file associated with an imported chat";
  `import_id` is the `id` from `messages.HistoryImport` returned by init.
- `startHistoryImport` — "To be called only after initializing the import with
  messages.initHistoryImport ***and uploading all files using***
  messages.uploadImportedMedia." Imported messages appear in history carrying a
  `fwd_from` `MessageFwdHeader` with `imported = true`.

### Canonical order (from /api/import)
```
checkHistoryImport(import_head=first ≤100 lines)
  -> checkHistoryImportPeer(peer)
  -> initHistoryImport(peer, file=export_file_InputFile, media_count=N)
  -> uploadImportedMedia(peer, import_id, file_name, media)   # x N, all media
  -> startHistoryImport(peer, import_id)
```

### TL a.k.a. `MessageFwdHeader` (the import marker on every imported message)
```
messageFwdHeader #4e4df4bb
  flags: #
  imported:   flags .7? true    # <- the official "this msg came from an import" flag
  from_id:    flags .0? Peer
  from_name:  flags .5? string
  date:       int               # the original message date
  channel_post / post_author / saved_from_peer / saved_from_msg_id /
  saved_from_id / saved_from_name / saved_date / psa_type ...
```
`/api/import`: "Imported messages will show in the chat history as messages
containing a `fwd_from` `messageFwdHeader` constructor with the `imported` flag."

---

## 2. The chat-export file format

Telegram accepts foreign-chat-export files in the **WhatsApp/ChatExport**
line format. This is the only format the server parses. It is primarily
detected from the **first ≤100 lines** (`checkHistoryImport` reads only the head).

### 2.1 Line grammar
`sender` is `First Name` (private chat). Line shape for a message:

```
DD/MM/YYYY, HH:MM - Sender: message text
```
Media message (attachment + optional caption on the SAME line):
```
DD/MM/YYYY, HH:MM - Sender: <Attached: filename.ext> caption text ...
```
Rules established:
- `DD/MM/YYYY, HH:MM` is **minute precision**, naive (no timezone suffix).
- The caption, when present, is text that follows the `<Attached: …>` marker on the
  **same** line — the server must attach it as `message.message` of the media
  message. A caption on a separate line becomes a separate text message (measured).
- The media marker's filename is what associates the media line with a specific
  uploaded file (section 4).

### 2.2 Validation we MUST do (independent of our serializer)
Feed `checkHistoryImport` the first ≤100 lines and require `HistoryImportParsed.pm == true`
(the export came from a private chat) for the A<->B private target. If `pm` is not
set, the file is not being recognized as a private-chat export and we must stop.

---

## 3. Date/time: Asia/Tehran is the source display zone

- `source.message.date` is the authoritative **absolute UTC instant**.
- The human/display timezone is `Asia/Tehran`; convert with IANA
  `ZoneInfo("Asia/Tehran")` applied at the historical date (Tehran had DST;
  offset is +03:30 or +04:30, never hard-coded).
- The export-file timestamp is written in the **naive `DD/MM/YYYY, HH:MM`** of the
  source's local wall-clock: `tehran_local(source_utc).strftime("%d/%m/%Y, %H:%M")`.
  Example (source 5307): `2015-12-31T20:35:57Z` -> Tehran `2016-01-01 00:05:57 +03:30`
  -> file `01/01/2016, 00:05`.
- Seconds are not representable in this format (minute precision = unavoidable).
- Measured server behavior (live, this project): the server interprets the naive
  file time with a **fixed +03:30 offset** and stores the matching UTC instant.
  On standard-offset dates this is the correct minute (verified minute-exact).
  On Iranian DST-period dates (historical +04:30) the message lands **+1 hour late**
  because the parser ignores +04:30. This is a Telegram server behavior; we report
  it (never patch `message.date` post-import).

---

## 4. Media: the still-unresolved attachment bug

### 4.1 What we did (real E2E) and what happened
- Package: 150 messages, 101 media files, 101 `<Attached: …>` markers, `media_count=101`.
- `checkHistoryImport / checkHistoryImportPeer / initHistoryImport(media_count=101)`
  all returned success; `uploadImportedMedia` called 101 times (each returned truthy);
  `startHistoryImport` returned truthy.
- Read-back of the actual target: **all imported target messages had `media == None`**
  (`MessageMediaEmpty`). Zero media ever attached.
- Later the server **rolled the whole import back**: 150 -> 1 message, observed by
  BOTH participants (A and B both read only the 1 remaining non-imported message).

Conclusion so far: accepting the RPCs and even a matching `media_count` does **not**
mean the server attached media. The attachment failed at the media level, and the
server treated the media-declared-but-not-present import as invalid and reverted it.

### 4.2 What the official API actually requires (schema, layer 223)
`uploadImportedMedia(peer, import_id, file_name: string, media: InputMedia) -> MessageMedia`
- `media: InputMedia` — must be an `InputMediaUploadedPhoto(file=InputFile)` for a
  photo, or `InputMediaUploadedDocument(file=InputFile, mime_type, attributes)` for
  a document/video/audio/sticker/GIF.
- The server associates the uploaded media with a message line in the export file
  **by filename**: the `file_name` passed here must correspond to the
  `<Attached: file_name>` marker in the export file. `file_name` is a plain string
  (no leading path in the schema).
- The `InputFile` inside the InputMedia must be a real uploaded-file handle
  (`upload.saveFilePart` -> `InputFile`, or `upload.saveBigFilePart` -> `InputFileBig`
  for files > 10 MB); Telethon's `client.upload_file` produces exactly these.

### 4.3 What is NOT derivable from Telegram Desktop
**Finding (important and contrary to the assumption this was a checkable client):**
The `dev` branch of `github.com/telegramdesktop/tdesktop` contains **no
history-import implementation** — the recursive source tree (not truncated, 6552
files) has zero files whose names reference import/history-import (only
`iv/editor/iv_editor_clipboard_import.*` and `import_theme` icons, unrelated).
The foreign-chat history import is driven by the Telegram **server**; there is no
client-side reference code in tdesktop to replicate. We therefore establish the
media-attachment mechanism by (a) the official schema and (b) a controlled
single-media probe, not by client source.

Hypotheses ranked for why media did not attach (to be resolved by the probe):
1. **Wrong/incomplete `media` payload** — e.g. `InputMediaUploadedDocument`
   missing required `attributes`, or a photo sent as a document, or `mime_type`
   wrong; the server drops it. (Most likely if per-file `MessageMedia` returned
   `MessageMediaEmpty`.)
2. **Filename mismatch** — `file_name` passed to `uploadImportedMedia` differs from
   the `<Attached: …>` marker (e.g. sanitized/renamed), so the server cannot bind
   the upload to any message line.
3. **Marker syntax wrong** — the server does not recognize `<Attached: …>` as media
   at all (needs exact casing/format from a real WhatsApp/ChatExport file).
4. **`checkHistoryImport` did not yield `pm=true`**, so the server parsed the file
   as a non-private/unknown export and ignored media.
5. **Media uploaded out of order / import_id mismatch** — less likely (we used the
   returned id), but the probe logs the exact id.

The probe (`recovery_v2/import_media_probe.py`) is designed to discriminate 1–5 by
(a) asserting `HistoryImportParsed.pm`, (b) logging the exact serialized `InputMedia`
and the exact returned `MessageMedia` for a single upload, and (c) reading the target
at T0/T1/T2/T3 for durability.

---

## 5. Feature-by-feature TL treatment (as the API allows)

- **Text**: `message.message` -> `target.message.message` (entity rendering differs).
- **Photo**: `MessageMediaPhoto`; upload via `InputMediaUploadedPhoto(file)`.
- **Video**: `MessageMediaDocument` + `DocumentAttributeVideo`; upload via
  `InputMediaUploadedDocument(file, mime_type, attributes=[DocumentAttributeVideo])`.
- **Animation/GIF**: `MessageMediaDocument` + `DocumentAttributeAnimated`; upload as
  document with `DocumentAttributeAnimated`.
- **Audio**: `MessageMediaDocument` + `DocumentAttributeAudio(voice=false)` + performer/title.
- **Voice**: same + `DocumentAttributeAudio(voice=true)`.
- **Document**: `MessageMediaDocument` + `DocumentAttributeFilename(file_name)`.
- **Sticker**: `MessageMediaDocument` + `DocumentAttributeSticker(alt, stickerset)`;
  a generic WEBP/`.tgs` without `DocumentAttributeSticker` is a document, not a
  sticker. Whether import preserves a real sticker set is UNSUPPORTED_BY_TELEGRAM
  unless the probe proves otherwise.
- **Album/grouped_id**: the import file has **no grouped_id concept**; multiple
  consecutive `<Attached: …>` lines by the same sender at the same time MIGHT be
  grouped by the server, but the official API documents no grouped_id preservation.
  Treat album restoration as UNSUPPORTED_BY_TELEGRAM unless a probe shows grouping.
- **Caption**: must be text appended to the `<Attached: …>` marker on the SAME line.
- **Reply**: the import format has **no reply relationship**; `message.reply_to`
  cannot be restored by import. UNSUPPORTED_BY_TELEGRAM.
- **Reaction**: the import format has **no reaction representation**.
  UNSUPPORTED_BY_TELEGRAM (irreversible historical reactions).
- **Forward/provenance**: the server copies `fwd_from` metadata for forwarded
  sources as best it can; for non-forwarded sources the target gets the `imported`
  fwd header only.
- **Sender**: import always authors the message as the importing account
  (target `from_id` = importing user / null), never a restored peer. The only
  origin metadata Telegram supports is `fwd_from.from_name` (a display string) and
  `fwd_from.date`. We must NOT fabricate `target.from_id = source.from_id`; we MAY
  set `fwd_from.from_name` to the source sender's display identity **exactly as
  Telegram Desktop/compatible exports do** — but we had invented `user_<id>`; that
  placeholder is not established as correct by any official source and the probe
  will confirm what the server stores, not invent.

---

## 6. Durability and rollback detection (mandatory)

Prior import "succeeded" then vanished. Every import test must therefore sample the
target at:
- T0 immediately after `startHistoryImport`
- T1 after ~30 s
- T2 after ~2 min
- T3 after ~5 min

Each sample: message count, message ids, media count, `fwd_from.imported` flags,
via **raw MTProto paginated read of the FULL target history** (getHistory ≤100/page;
single-page reads undercount — a known trap here). If count drops to 0 (or below an
expected floor) after an earlier non-zero read, report
`SERVER_SIDE_ROLLBACK = YES` and stop importing further data.

---

## 7. Hard error semantics
- `checkHistoryImport` failing / `HistoryImportParsed.pm != true`  -> STOP (file not
  recognized as a private-chat export).
- `uploadImportedMedia` returning `MessageMediaEmpty` for a photo/document flag that
  media is NOT being attached -> STOP (diagnose the payload before proceeding).
- Any RPC `FLOOD_WAIT_*` -> back off, do not silently retry an import.
- `startHistoryImport == false` / an RPC error on commit -> FAIL, no messages imported.

---

## 8. Source-of-truth summary
- Import order and signatures: this file (from official schema + /api/import).
- Media payload details: `docs/TELEGRAM_IMPORT_MEDIA.md`.
- The controlled single-media probe: `recovery_v2/import_media_probe.py`, which is
  the gate before any multi-message import is allowed again.