# REAL IMPORT vs NEW-MESSAGE AUDIT

**Date:** 2026-09-01
**Scope:** `telegram_recovery_v2/` production engine (`src/recovery/`).
**Purpose:** prove the recovery path is a genuine Telegram **history import**, not
a disguised `sendMessage`/`sendMedia`/`forwardMessages` that creates new
current-time messages. This is a CODE-PATH audit. It does **not** prove live
server behavior — see the status column in
[FINAL_IMPORT_TRUTH_REPORT.md](FINAL_IMPORT_TRUTH_REPORT.md).

## Audit method

Authoritative grep of every `src/` file for current-time send substitutes, and
every `src/` file for the official history-import method names.

| Check | Result |
|---|---|
| `sendMessage` / `SendMessageRequest` in `src/` | **0 matches** |
| `sendMedia` / `SendMediaRequest` in `src/` | **0 matches** |
| `send_message` / `send_file` in `src/` | **0 matches** |
| `forwardMessages` / `ForwardMessagesRequest` in `src/` | **0 matches** |
| `copyMessages` / `CopyMessagesRequest` in `src/` | **0 matches** |
| `CheckHistoryImport(Request)` in `src/` | present — `importer.py:177` |
| `CheckHistoryImportPeer(Request)` in `src/` | present — `importer.py:182` |
| `initHistoryImport` / `InitHistoryImportRequest` | present — `importer.py:191` |
| `uploadImportedMedia` / `UploadImportedMediaRequest` | present — `importer.py:198` |
| `startHistoryImport` / `StartHistoryImportRequest` | present — `importer.py:203` |

## Production code-path trace

Frontend action: none yet in this v2 — CLI first (rule #55). Integration of the
dashboard is deferred until the live E2E proves a clean baseline; the dashboard
will call `TelegramRecoveryEngine` through a thin adapter.

Endpoint (prospective web): will call `TelegramRecoveryEngine`.

CLI (current): `recovery-v2 import` → `engine.import_package()` (`engine.py:217`)
→ `ImportEngine.run_import(...)` (`importer.py:207`) → in order:

1. `messages.checkHistoryImport(import_head=…)` — `importer.py:177`
2. `messages.checkHistoryImportPeer(peer)` — `importer.py:182`
3. `messages.initHistoryImport(peer, file, media_count)` — `importer.py:191` (returns `import_id`)
4. `messages.uploadImportedMedia(peer, import_id, file_name, media)` — `importer.py:198` (once per media file)
5. `messages.startHistoryImport(peer, import_id)` — `importer.py:203`

Service: `ImportEngine` / `import_package.py` module.

Telegram methods actually called in the RECOVERY IMPORT path:
`checkHistoryImport, checkHistoryImportPeer, initHistoryImport,
uploadImportedMedia, startHistoryImport`. Media bytes are uploaded with
`upload_file` (raw transport upload) and wrapped as `InputMediaUploadedPhoto` /
`InputMediaUploadedDocument` for `uploadImportedMedia` — **not** sent as
`sendMedia`.

## Where current-time sends ARE used (and must be labeled)

- `scripts/create_fixture.py` — the **source fixture builder**: it creates the
  A↔B conversation *in account A* using `send_message` / `send_file`. This is
  **not** part of the recovery import and is now labeled as such in the file
  header. It populates the SOURCE so the engine has something real to read.
- `reactions.reconstruct` uses `messages.sendReaction` — the documented,
  labeled **post-import reconstruction** step (rule #18), never a history-import
  substitute.

## Target message constructor trace (what the engine reads back)

After import, `engine.snapshot_target("after")` reads the real target via
`messages.getHistory` and builds a record per message that keeps: `id, from_id,
peer_id, date, edit_date, message(text), media, entities, reply_to, forward
(fwd_from incl. `imported`, `date`, `from_id`, `from_name`, `channel_post`),
grouped_id, reactions, views, forwards, flags`. The raw MTProto object is also
available via `tl_to_plain`. No client-side spoofing, no local DB/desktop cache
patching, no injected message ids.

## Timestamp classification (implemented, matches the required semantics)

`verifier._timestamp`:
- `EXACT` iff `abs(target.message.date − source.message.date) < 60s`
- `IMPORTED_METADATA_ONLY` iff `target.fwd_from.imported == true` **and**
  `target.fwd_from.date` is historical, while the visible `target.message.date`
  differs → i.e. only the forward metadata is historical
- `NOT_RESTORED` otherwise

`target.fwd_from.date` is **never** called the restored visible message date.

## Honest conclusion

The recovery **import path is genuine history import** — it does not silently
convert import into send-new-messages. Whether the official import yields a
historical `target.message.date` (CASE A/B, rule #25) is a **server-behavior
fact that can only be established by running against real Telegram**; it is
**not** established by this code audit or by the hermetic tests.