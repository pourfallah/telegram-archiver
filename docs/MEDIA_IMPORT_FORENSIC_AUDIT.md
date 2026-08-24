# MEDIA IMPORT FORENSIC AUDIT

Branch: `feature/historical-fidelity-import`
Date: 2026-08-24
Scope: full trace of one real photo and one sticker through the media pipeline,
plus five controlled live-import experiments against Telegram's servers.

---

## 1. Pipeline trace — photo (`photo_0.jpg`, David Rodriguez export)

| stage | what happens | evidence |
|---|---|---|
| SOURCE TELEGRAM MESSAGE | msg id 5669616 (Account A, chat 7768075024), `MessageMediaPhoto` | export messages.jsonl |
| EXPORT | Telethon `download_media()` → `/data/exports/_989394430100/David Rodriguez/archive/media/photo/photo_0.jpg` | file exists on disk |
| CANONICAL ARCHIVE | ndjson row has `media: [{filename, type: photo, size}]` | archive/messages/messages.ndjson |
| IMPORT FILE | line `8/20/2026, 10:19 AM - First Dev.: <attached: photo_0.jpg>` (WhatsApp format) | import/import.txt |
| initHistoryImport | `media_count=1` declared; returns `import_id` | worker log job #26 |
| uploadImportedMedia | `UploadImportedMediaRequest(peer, import_id, file_name='photo_0.jpg', media=InputMediaUploadedPhoto(InputFile))` → **returns real `MessageMediaPhoto` token** with photo id 4927416168692256028 | worker log 11:07:40 |
| startHistoryImport | returns `True` | worker log |
| TARGET MESSAGE | id 457/497/etc: `text='<attached: photo_0.jpg>'`, `media=None` | live `get_messages` reads |

**Failure point:** between the accepted `uploadImportedMedia` token and the
server-side materialization of the imported block. The server never binds the
uploaded media object to the placeholder line.

Identical trace for the sticker (`sticker_160416.webm`,
`InputMediaUploadedDocument(mime=video/webm, [DocumentAttributeFilename])`) —
same result.

## 2. Controlled experiments (all live, test peer @pourfallah)

| # | variant | marker syntax | media kind | upload | result in target |
|---|---|---|---|---|---|
| 1 (job 25) | dot date format | `<attached: photo_0.jpg>` | photo + webm doc | ✅ tokens | literal text, `media=None` |
| 2 (job 26) | WhatsApp date format | same | same | ✅ | literal text |
| 3 (variantC) | no-space marker `<attached:photo_0.jpg>` | photo uploaded as `InputMediaUploadedDocument(image/jpeg)` | ✅ | literal text |
| 4 (variantD) | sticker as doc w/ emoji attr | n/a | — | `IMPORT_ID_INVALID` (import expired between runs) |
| 5 (variantE) | WA-style filename `00000001-PHOTO-...jpg` + 8 s delay before start | photo | ✅ | literal text |

## 3. Critical observation — even first-party imports lose media

The user's own chat contains a **real WhatsApp→Telegram migration** performed by
WhatsApp's official "Move chats" flow:

```
[8/20/2026 10:19 AM] First Dev.: [ ❤️ Sticker ]
[8/20/2026 10:19 AM] First Dev.: [ Photo ]
```

Those bracket placeholders are **literal text with `media=None`** when read via
MTProto. Even Telegram's own first-party flow reduces imported private-chat
media to text placeholders.

## 4. Telegram Desktop source research

- Current `dev` tree (Aug 2026): `HistoryImport` appears **only** in
  `mtproto/scheme/api.tl` (schema) and
  `history/view/history_view_send_action.cpp` (the "importing…" typing indicator).
  Verified: `git grep HistoryImport origin/dev` → those two files only.
- The chat-import UI module existed briefly (2020–2021 era) and was **removed**
  from the codebase. Commits that remain: `19455d44d` (2021-01-25, "Add support
  for imported messages" — display side only), `7410c1fc7` ("Fix display of
  imported messages in private chats"), `894e7c582`.
- There is therefore **no official client implementation of
  `uploadImportedMedia` to copy from**, and none ever shipped in a stable tdesktop.

## 5. Conclusion

The `<attached: …>`-as-text outcome is **not a bug in our pipeline**: our
implementation performs every documented RPC correctly, in the correct order,
with correct parameters, and receives success responses. The server accepts the
media tokens but does not bind them to the imported message lines for this API
surface. Combined with the first-party WhatsApp-import behavior above, media
restoration through `messages.*HistoryImport*` is **not achievable** — it is a
Telegram platform limitation, not an implementation defect.

What IS achievable and implemented:
- real historical dates/times ✅ (verified)
- full text/order/sender fidelity ✅
- complete media preservation in the canonical archive (files + SHA-256 + metadata)
- per-job media debug logs and honest verification (`MEDIA_FAILED` classification)

Documented in `docs/MEDIA_IMPORT_FORENSIC_AUDIT.md` (this file),
`docs/RECOVERY_FIDELITY.md`, and surfaced in the Step 10 report.
