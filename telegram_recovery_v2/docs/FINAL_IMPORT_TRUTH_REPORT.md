# FINAL IMPORT TRUTH REPORT

**Generated:** 2026-09-01
**Evidence level key:**
- `PROVEN-CODE` — the production operation is verified in the source (grep).
- `PENDING-LIVE` — the target result has NOT been measured on a real Telegram
  server yet (no credentials in this environment). **A `PENDING-LIVE` cell is an
  unproven claim, not a header claim.**

The only authoritative validation is the **actual Telegram target message
objects** read after recovery. Every row below that depends on that reading is
`PENDING-LIVE` until `scripts/minimal_import_tests.py` is run with real
credentials and this table is updated from its output.

| FEATURE | SOURCE (archive) | PRODUCTION OPERATION | TARGET RESULT | STATUS | EVIDENCE |
|---|---|---|---|---|---|
| TIMESTAMP | `message.date` | official history import (`initHistoryImport`→`startHistoryImport`) | `message.date` vs `fwd_from.date` vs source-date not yet read | **PENDING-LIVE** | `minimal_import_tests.py timestamp` output |
| SENDER | `from_id` (peer id) | official import + `from_name`/`fwd_from` metadata | `target.from_id` / `fwd_from.from_name` not yet read | **PENDING-LIVE** | verifier `SENDER_*` |
| PHOTO | `MessageMediaPhoto` + all PhotoSize | `uploadImportedMedia` | target `MessageMediaPhoto` not yet read | **PENDING-LIVE** | `minimal_import_tests.py media` |
| CAPTION | `message.message` kept ON the media record | official import of `_chat.txt` line block | attached vs separate not yet read | **PENDING-LIVE** | `CAPTION_*` |
| STICKER | `DocumentAttributeSticker` (alt, stickerset) | `uploadImportedMedia` | target `DocumentAttributeSticker` vs filename-only not yet read | **PENDING-LIVE** | `minimal_import_tests.py sticker`; `DOCUMENT_ONLY` if only WEBP+filename |
| VIDEO | `DocumentAttributeVideo` | `uploadImportedMedia` | target `DocumentAttributeVideo` not yet read | **PENDING-LIVE** | `media_class(…, "video")` |
| GIF/ANIM | `DocumentAttributeAnimated` | `uploadImportedMedia` | target `DocumentAttributeAnimated` not yet read | **PENDING-LIVE** | `media_class(…, "animation")` |
| AUDIO | `DocumentAttributeAudio` (voice=False) | `uploadImportedMedia` | target `DocumentAttributeAudio` not yet read | **PENDING-LIVE** | `media_class(…, "audio")` |
| DOCUMENT | `DocumentAttributeFilename` | `uploadImportedMedia` | target `MessageMediaDocument` not yet read | **PENDING-LIVE** | `media_class(…, "document")` |
| REPLY | `MessageReplyHeader` (reply_to_msg_id, quote…) | official import (no invented param) | target `reply_to` not yet read | **PENDING-LIVE** | `minimal_import_tests.py reply`; `REPLY_*` |
| REACTION | `getMessageReactionsList` → (reactor, reaction) | **post-import** `sendReaction` per actor (documented reconstruction) | target `getMessagesReactions` not yet read | **PENDING-LIVE** | `minimal_import_tests.py reaction` |
| ALBUM | real `grouped_id` | official import | target `grouped_id` not yet read | **PENDING-LIVE** | `GROUP_*`; `GROUP_FLATTENED` if lost |
| FORWARD | `fwd_from` (imported, channel_post…) | official import of `_chat.txt` | target `fwd_from` not yet read | **PENDING-LIVE** | verifier `_forward` |

## Production operation vs target result

- **Production operation** column is `PROVEN-CODE` for every import-related row
  (the 5 official methods, verified by grep in
  [REAL_IMPORT_VS_NEW_MESSAGE_AUDIT.md](REAL_IMPORT_VS_NEW_MESSAGE_AUDIT.md)).
  No `sendMessage`/`sendMedia`/`forwardMessages`/`copyMessages` substitution
  exists in the recovery path.
- **Target result** column is measured from the real server only, so it is
  `PENDING-LIVE` until `scripts/minimal_import_tests.py` runs.

## Mandatory controlled test (rule #6, #14)

`scripts/minimal_import_tests.py timestamp` must print, for ONE source message:

```
SOURCE DATE:             <source.message.date>
TARGET message.date:     <target.message.date>
TARGET fwd_from.date:    <target.fwd_from.date>
TARGET fwd_from.imported:<bool>
TARGET from_id:          <target.from_id>
RESULT:                  TIMESTAMP_EXACT | IMPORTED_METADATA_ONLY | NOT_RESTORED
```

`TIMESTAMP_EXACT` **only** if `TARGET message.date == SOURCE message.date` **and**
the message is positioned accordingly in the target chat. If only
`fwd_from.date` matches → `IMPORTED_METADATA_ONLY`. There is no documented
`initHistoryImport` parameter that sets `message.date`; we do not invent one.

## Decision tree (rule #25) — to be filled after the live run

- **CASE A** official import yields historical `target.message.date` + media →
  continue improving the official import.
- **CASE B** `target.message.date = import time`, only `fwd_from.date` historical
  → document as a Telegram import limitation for the visible timeline; do not fake.
- **CASE C** works in minimal reproducer but not the app → production bug; find
  the code-path difference.
- **CASE D** media types differ per-type → implement per-media behavior.

No row is marked complete until measured. See [FIDELITY_CLASSES.md](FIDELITY_CLASSES.md)
for the exact label semantics.