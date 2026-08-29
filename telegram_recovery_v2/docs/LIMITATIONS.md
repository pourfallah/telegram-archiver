# Limitations / Capability Matrix (telegram_recovery_v2)

**Source of truth**: FINAL_REPORT.json / FINAL_REPORT.html of the clean E2E run
`recovery_v2_20260829_125341_cb958e` (pristine archive recovered_v2_20260829_091021_548599
cloned into a fresh run; 68-line package; 36 media tokens; import_id 1328251734932446105;
target re-read after materialization settled). Every verdict below was read from
ACTUAL target Telegram Message objects — never from upload RPC success or local files.

| FEATURE | SOURCE AVAILABLE | IMPORT POSSIBLE | POST-IMPORT RECONSTRUCTION | VERIFIED ON TARGET | STATUS |
|---|---|---|---|---|---|
| TEXT | yes | yes | n/a | yes (text preserved verbatim) | EXACT |
| FORMATTING (bold/italic/code/spoiler/url) | yes (entities archived) | text yes, entities NO | possible via editMessage with entities | text preserved, entity marks lost | PARTIAL / ENTITY_STRIPPED |
| SENDER | yes | fwd_from.imported metadata (original sender id + imported flag) | n/a | 45 SENDER_METADATA_ONLY, 14 SENDER_EXACT | PARTIAL |
| TIMESTAMP | yes | yes (write file times in TARGET account tz; +210 min) | n/a | 54/62 TIMESTAMP_EXACT; direct target read shows dates == source instants | EXACT |
| PHOTO | yes | yes | n/a | 12/12 MessageMediaPhoto | EXACT |
| PHOTO+CAPTION | yes | media yes; caption as +1s separate message | possible via editMessage merge | 6/6 captions present as CAPTION_SEPARATE | PARTIAL (documented WA parity) |
| VIDEO | yes | yes | n/a | direct count 4/4 MessageMediaDocument+Video | EXACT |
| GIF | yes | yes | n/a | 2/2 DocumentAttributeAnimated | EXACT |
| AUDIO | yes | yes | n/a | 5/5 MessageMediaDocument+Audio | EXACT |
| VOICE | yes | yes (voice=True attr) | n/a | 3/3 voice=True | EXACT |
| DOCUMENT | yes | yes | n/a | 6/6 (pdf + image files) | EXACT |
| STICKER (.tgs) | yes (bytes+attrs) | token binds but media DROPPED → EMPTY message | NOT possible via import API | 0/4 → EMPTY | FAILED (honest; same in old engine) |
| CUSTOM EMOJI | NOT_AVAILABLE (fixture sender lacks Premium) | — | — | — | NOT_AVAILABLE |
| REPLY | yes (reply_to archived) | NO (import file format has no reply syntax) | possible: delete orphan + send_message(reply_to=...) — CURRENT-TIME reconstruction, not historical | source 3 reply_to, target 0 | REPLY_ARCHIVAL_ONLY |
| FORWARD | yes (fwd_from archived) | yes (music/audio forwards; fwd_from.date == source instant) | n/a | fwd_from.date matches | EXACT |
| REACTION | yes (reactor id from raw recent_reactions) | NO (import never carries reactions) | YES: per-reactor session messages.sendReaction | 8/8 verified via getMessagesReactions (same reactor, same emoji, same target) | RECONSTRUCTED 8/8 |
| ALBUM (grouped_id) | yes | NO (grouped_id lost) | NOT possible via import/uploadImportedMedia | target grouped_id absent | GROUP_FLATTENED |

## Hard limits of the official import API (evidence-based)

1. **Replies cannot be imported** — the accepted WhatsApp file syntax has no reply
   field; `messageReplyHeader` is never produced on the target. (observed: source
   3 reply_to → target 0; both engines agree)
2. **Album grouping is not preserved** — grouped_id is not part of the import
   syntax. (observed: source grouped albums → target without grouped_id)
3. **Text entities are not preserved** — the parser creates its own text; only
   auto-detected hashtags/mentions materialize. (observed: BOLD/code/url entities
   stripped, hashtags present)
4. **.tgs animated stickers are dropped entirely** — token binds (uploadImportedMedia
   returns MessageMediaDocument) but the target message materializes EMPTY
   (media=None, text='') even with octet-stream mime / no sticker attribute.
   (observed 4/4; identical behavior in both old and new engines)
5. **Reactions are never imported** — reconstruction via per-reactor
   messages.sendReaction works and was verified 8/8, but reaction dates are lost
   (API does not accept them) and it is CURRENT-TIME post-import reconstruction.
6. **Captions import as +1s separate messages** — attaching a caption to the media
   line breaks media binding entirely (live-proven). This matches real WhatsApp
   export behavior, but a single source message with caption becomes TWO target
   messages.
7. **Timestamps are interpreted in the importing account's timezone** — the import
   file must contain target-tz wall-clock times (UTC +3:30 for these accounts),
   otherwise every visible date shifts by the tz offset.

## What the engine does NOT do

- No `messages.forwardMessages` / send-new-message substitutes for history import.
- No WhatsApp conversion layer — the WA file is the IDENTICAL format the official
  API requires (checkHistoryImport).
- No "full restore" claims: statuses are EXACT / RECONSTRUCTED / PARTIAL /
  ARCHIVAL_ONLY / FAILED only, from target reads.