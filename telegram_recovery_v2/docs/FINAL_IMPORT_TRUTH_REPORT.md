# FINAL IMPORT TRUTH REPORT (telegram_recovery_v2)

Run: **recovery_v2_20260829_125341_cb958e** — the clean, corrected E2E.
Source: pristine archive (from A read 09:05, before any of this session's
experiments) cloned into a fresh run dir via scripts/clone_run.py (spec 53/61:
new run_id, no artifact reuse). Package: 68 lines / 36 media, timestamps
pre-shifted +210 min (target account tz, proven requirement). Import:
initHistoryImport → 36× uploadImportedMedia → startHistoryImport
(import_id 1328251734932446105). Target re-read after materialization settled.

Earlier evidence run recovery_v2_20260829_121240_6a9ab6 exposed engine bugs;
the fixes are listed below with the corrected results from 125341.

## Protocol behavior PROVEN live (not assumptions)

1. **Historical timestamps DO restore** — but the file must carry the target
   account's LOCAL wall-clock. Telegram parses naive DD/MM/YYYY HH:MM:SS in the
   account tz (observed UTC+3:30). First run wrote UTC wall-clock → every
   visible date −3:30h (file 07:52:20 → visible 04:22:20). With +210 shift the
   target reads 07:52:20 again (visible == fwd_from.date == source instant).
2. **Materialization delay**: after startHistoryImport dates/media settle over
   minutes; verification must poll until stable (we polled 67/67 twice).
3. **Media binds ONLY on bare `<attached: NAME>` lines** with ONE uploaded token
   per line, exact file_name match (tdlib MessageImportManager mechanism).
4. **Captions** must be emitted as a separate +1s message (attached captions
   break binding entirely). Result: CAPTION_SEPARATE — matches real WhatsApp
   import behavior; a media+caption source message becomes two target messages.
5. **uploadImportedMedia's return token is NOT diagnostic** — all 36 returned
   MessageMediaPhoto/MessageMediaDocument yet only 32 media materialized; the 4
   failures were .tgs (EMPTY). Only the materialized target object counts.
6. **.tgs animated stickers do not materialize at all** (EMPTY, media=None even
   with generic octet-stream mime, no sticker attribute). IDENTICAL behavior in
   the old engine (its run_21 verification even classified tgs as
   "STICKER_SEMANTIC_PARTIAL" against WRONG matched targets — a false positive
   from lenient mapping). Honest classification: FAILED / ARCHIVAL_ONLY.
7. **Replies are not importable** (file syntax has no reply field); albums lose
   grouped_id; text entities are stripped (only auto hashtags/mentions).
8. **Reactions are never imported**; per-reactor messages.sendReaction from each
   reactor's own session reconstructs them (verified 8/8 on target with
   messages.getMessagesReactions: same reactor, same emoji, same target).

## Engine bugs found & fixed (all live-verified in 125341)

| Bug | Root cause | Fix | Verified |
|---|---|---|---|
| 36 media imported as literal `<attached:>` text | build_package never wrote media_attach_map.json → 0 tokens before start | attach map written; run_import hard-aborts if declared media count != resolved specs, and refuses re-start of a started import | 36/36 bound, 32 materialized |
| visible dates −3:30h | UTC wall-clock in file; parser uses account tz | `_wa_ts` pre-shifts +210 (default; configurable) | TIMESTAMP_EXACT on target |
| resume crash (`list indices…`) | ImportState.uploaded_files dict→list on save, load didn't convert back | load() converts list→dict | resume works |
| voice (.ogg) literal text | attach name mismatch: serializer used filename ext (.ogg), attach map used local_file ext (.oga) | single `_ext_for` from local_file in both build_import_file and build_package | 3/3 VOICE materialized |
| .tgs EMPTY | x-tgsticker mime / sticker attr | upload as generic octet-stream doc, no sticker attr | still EMPTY (API limit, honest) |
| image/gif docs EMPTY | animated attr on image/gif broke binding | generic octet-stream, filename only | 3/3 DOCUMENT materialized |
| voice attr absent | audio path always voice=False | media_type threaded → DocumentAttributeAudio(voice=True) | 3/3 VOICE |
| mapper mis-mapping | sequence bias, no timestamp signal | timestamp+media-type+exact-text scoring; 1s-unique lock | 59/62 mapped |
| caption counted LOST | verifier didn't know +1s caption | classify_caption checks whole-target caption texts | CAPTION_SEPARATE 6/6 |

## Final capability verdict (target objects, run 125341)

- TEXT: EXACT — 14/14 verified rows; target text verbatim.
- FORMATTING: PARTIAL — text kept, MessageEntities stripped (hashtags/mentions
  survive as parser artifacts). No editMessage reconstruction attempted (spec 19
  dtype: current-time reconstruction must be labeled as such).
- SENDER: PARTIAL — 45 SENDER_METADATA_ONLY (fwd_from.imported carries original
  sender metadata), 14 SENDER_EXACT; 3 mapper artifacts.
- TIMESTAMP: EXACT — 54/62 mapped rows exact; direct target read: all imported
  messages dated at source instants (07:52:20–09:02:00). 8 non-exact rows are
  4 .tgs EMPTY + 3 mapper cross-maps + 1 floodwait artifact.
- PHOTO: EXACT — 12/12 MessageMediaPhoto (direct constructor count).
- PHOTO+CAPTION: PARTIAL — media EXACT; caption CAPTION_SEPARATE 6/6 (the
  documented WhatsApp-import splitting).
- VIDEO: EXACT — 4/4 MessageMediaDocument+DocumentAttributeVideo.
- GIF: EXACT — 2/2 DocumentAttributeAnimated.
- AUDIO: EXACT — 5/5 MessageMediaDocument+DocumentAttributeAudio.
- VOICE: EXACT — 3/3 with voice=True.
- DOCUMENT: EXACT — 6/6 (3 pdf + 3 image/gif-as-doc).
- STICKER: FAILED — 4/4 .tgs materialize EMPTY (media=None). File bytes +
  attributes preserved in the archive only.
- REPLY: NOT_RESTORED — target has 0 reply headers (source 3). ARCHIVAL_ONLY.
- FORWARD: EXACT — forwarded music/audio: fwd_from.date == source instant.
- REACTION: RECONSTRUCTED 8/8 — per-reactor sessions, verified 8/8.
- ALBUM: GROUP_FLATTENED — target grouped_id absent.
- CUSTOM EMOJI: NOT_AVAILABLE — fixture sender lacks Telegram Premium.

## Old vs new engine — same answer on the hard limits

Both engines independently produce: tgs EMPTY, replies NOT_RESTORED,
captions CAPTION_SEPARATE, reactions reconstructable, timestamps exact with
tz-corrected files, media 32/36 materialized via official import. The old
engine's reports over-claimed (STICKER_SEMANTIC_PARTIAL from wrong mapping;
timestamp "TIMESTAMP_RESTORED 8/8" measured during its own fixture window).
The v2 engine's classifications come from direct target-object reads.

## Honest scope statement

This engine restores the SOURCE CONVERSATION into the A↔B peer via the OFFICIAL
history-import API: text, timestamps, senders (metadata), photos, videos, GIFs,
audio, voice, documents, forwards; captions as separate messages; reactions
via per-reactor reconstruction. It does NOT restore entity formatting,
replies, or album grouping, and .tgs stickers do not materialize. No
current-time send-message substitutes were used. Statuses: EXACT /
RECONSTRUCTED / PARTIAL / ARCHIVAL_ONLY / FAILED — never "full restore".