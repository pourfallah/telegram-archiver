# MEDIA IMPORT PROTOCOL MATRIX

Branch: `investigate-real-media-import`
Date: 2026-08-24
Status: **MEDIA IMPORT WORKS — root cause was the import-file format**

## Executive finding

Our earlier conclusion ("media import is broken for everyone") was **wrong**. It
was **answer A: our implementation's file format was incorrect**. With the
correct real-WhatsApp export syntax, Telegram's server attaches uploaded media
to imported messages as genuine `MessageMediaPhoto` / `MessageMediaDocument`
objects.

## The format that works (verified live)

```
[05/01/2024, 10:00:30] Jane Smith: <attached: 00000042-PHOTO-2024-01-05-10-00-30.jpg>
```

- **Brackets** around the timestamp: `[...]`
- **DD/MM/YYYY** (dot-free, day-first), **24-hour `HH:MM:SS` with seconds**
- Sender + `: ` then message text
- Media marker `<attached: FILENAME>` where FILENAME **exactly** matches the
  `file_name` passed to `messages.uploadImportedMedia`

Our previous formats — `8/20/2026, 10:19 AM` (no brackets, US order, AM/PM) and
`20.08.2026 06:49` (no brackets) — made the server treat the `<attached: …>`
line as literal text.

## Matrix (live results, test peer @pourfallah)

| Media Type | InputMedia used | uploadImportedMedia result | Target MessageMedia | Supported |
|---|---|---|---|---|
| photo | `InputMediaUploadedPhoto` | `MessageMediaPhoto` | **`MessageMediaPhoto`** (text `''`) | ✅ YES |
| photo + caption | `InputMediaUploadedPhoto` + caption line | `MessageMediaPhoto` | `MessageMediaPhoto` (own msg) + separate caption msg | ✅ photo restored |
| document (.pdf) | `InputMediaUploadedDocument` | `MessageMediaDocument` | pending matrix run | ✅ (expect) |
| video | `InputMediaUploadedDocument` | `MessageMediaDocument` | pending | ✅ (expect) |
| sticker (webm) | `InputMediaUploadedDocument` | `MessageMediaDocument` | pending | ⚠️ check attrs |
| GIF / audio / voice | same doc path | — | — | identical mechanism |

*(Matrix run in progress; final per-row results appended below on completion.)*

## Root-cause chain (photo)

SOURCE TM message (msg 5669616, `MessageMediaPhoto`)
→ export `archive/media/photo/photo_0.jpg`
→ line `[05/01/2024, 10:00:30] Jane Smith: <attached: 00000042-PHOTO-...jpg>`
→ `initHistoryImport(media_count=1)` → import_id
→ `uploadImportedMedia(peer, id, file_name=WA_NAME, InputMediaUploadedPhoto)` → `MessageMediaPhoto`
→ `startHistoryImport`
→ target msg `media=MessageMediaPhoto`, `text=''` ✅

## Why prior tests failed

5 earlier variants used non-bracket / non-seconds / US-date syntax → server
parsed the media marker as ordinary text. The 6th (this one) used exact WA syntax
→ media attached. **Formatting, not platform capability.**

## Association mechanism (answering the investigation's central question)

From `tdlib` (official) `MessageImportManager.cpp`:
- The server associates each uploaded media to a message row by the **exact
  `file_name` string** appearing in the import file's `<attached: FILENAME>` line.
- `file_name` is the file's basename. `InputMediaUploadedPhoto` / 
  `InputMediaUploadedDocument(+DocumentAttributeFilename)` identify the media.
- Order/timing: upload all attachments, then `startHistoryImport`.