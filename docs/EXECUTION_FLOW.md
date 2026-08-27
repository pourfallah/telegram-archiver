# EXECUTION FLOW

Runtime flow for each major operation: USER → FRONTEND → ENDPOINT → SERVICE → WORKER → TELEGRAM → DB → RESULT → FRONTEND.

---

## 1. Export (Account A = source of truth)

```
USER clicks "Start Export"
  └─ Exports.tsx → POST /api/exports {account_id, chat, format, include_media}
       └─ api/exports.py → create ChatExport(row, queued) → Celery export.run.delay(id)
            └─ worker tasks.py::export.run
                 └─ export_engine.py::ExportEngine.run(id)
                      ├─ _acquire_guard: session_manager.acquire_client(account A)
                      ├─ total_messages
                      ├─ loop: _fetch_batch(client.get_messages)   [TELEGRAM]
                      │    ├─ rows = [message_to_dict(m)]           (telegram_utils)
                      │    ├─ enrich_reaction_users                 (getMessageReactionsList)
                      │    ├─ _persist_batch: insert Message/MediaFile rows   [DB]
                      │    ├─ media_downloader.download_batch       [TELEGRAM media]
                      │    └─ JsonLineWriter → messages.jsonl
                      └─ _finalize:
                           ├─ assemble_json_archive → messages.json
                           ├─ sqlite/html writers
                           └─ verify_export (export_verification)
                                └─ re-read live chat [TELEGRAM], compare per-field vs archive
                                     └─ set export.verified=TRUE/FALSE, write EXPORT_VERIFICATION.json [DB]
  └─ GET /api/exports/{id} → frontend shows status + VERIFIED badge
```

DB state: `chat_exports(verified, verification, export_dir)`, `messages`, `media_files`.
Export is gated: only `verified=TRUE` exports can feed import.

---

## 2. Package generation (import.txt)

Produced inside the import worker (not a separate UI step):

```
_ run_import_async
  └─ build_import_file(export_dir, import.txt, limit, tz_offset_minutes)
       └─ import_serializer.py::build_import_file
            ├─ load canonical archive messages (oldest-first)
            ├─ for each message: "[DD/MM/YYYY, HH:MM:SS] - Sender: <attached: FILE>\n<caption>"
            │    (text-only: "[DD/MM/YYYY, HH:MM:SS] - Sender: text")
            └─ stats {messages, media_refs, users, date_min, date_max}
  └─ parse_import_head (first 100 lines)
```

---

## 3. Test import

```
TestImport.tsx → POST /api/import/{account}/test-import {export_id, count, ...}
  └─ imports.py::start_test_import
       ├─ gate export.verified
       ├─ create ImportJob(status=queued) [DB]
       └─ run_import.delay(job.id)
            └─ same worker path as real import (SECTION below), message_limit=count
  └─ ImportJobDetail polls GET /api/import/jobs/{id}
```

Identical code path to real import; only `options.test_mode=True` differs.

---

## 4. Real import

```
RealImport.tsx::handleStep9 → POST /api/import/start-real
  └─ imports.py::start_real_import
       ├─ gate: export.verified + verification.status=="PASS" (409 export_not_verified)
       ├─ build_canonical_archive if missing
       ├─ create ImportJob [DB]
       └─ run_import.delay(job.id)

worker import_tasks.py::run_import → _run_import_async(job_id)
  ├─ [DB] load job, export, target account
  ├─ acquire client for TARGET account (B) via session_manager
  ├─ resolve target peer (by target_peer_id or contact_identifier)
  ├─ Phase peer_checking: importer.check_history_import_peer(peer)      [TELEGRAM]
  ├─ Phase building_import_file: build_import_file() → import.txt
  ├─ Phase check_import_format: importer.check_history_import(head)     [TELEGRAM]
  ├─ Phase init_history_import: importer.init_history_import(peer, file, media_count)  [TELEGRAM → import_id]
  ├─ Phase media_uploading:
  │    └─ build_media_specs_from_archive(export_dir, import_text) → specs
  │    └─ TelegramImportedMediaService(client).upload_imported_media(peer, import_id, spec)
  │         └─ client.upload_file (file)  +  messages.UploadImportedMediaRequest   [TELEGRAM]
  ├─ snapshot target BEFORE (delta ids)                                [TELEGRAM]
  ├─ Phase starting_import: importer.start_history_import(peer, import_id)       [TELEGRAM]
  ├─ Phase verifying:
  │    ├─ poll get_messages until imported count stabilizes (~5 min)
  │    ├─ target_dicts = [message_to_dict(m)] + fwd_from metadata + is_new delta
  │    └─ run_verification(archive, new_target_dicts)  → report [DB, report files]
  │         └─ build_fidelity/handling reports
  ├─ Phase B reconstruction:
  │    └─ reconstruction.plan_reactions + reconstruct_reactions (multi-session)  [TELEGRAM]
  └─ job.status = completed | partial | failed; write IMPORT_DEBUG_LOG.json
```

---

## 5. Verification

```
worker → import_verification.run_verification(source_archive, new_target_dicts)
  └─ compares ONLY the NEW target messages (snapshot delta)
       per-message: sender (IDENTICAL/MAPPED_TO_IMPORTER/MISMATCH),
                    timestamp (TIMESTAMP_RESTORED/IMPORTED_METADATA_ONLY/NOT_RESTORED),
                    media (PHOTO_EXACT/STICKER_EXACT/AUDIO_EXACT/.../DOCUMENT_EXACT/UNMATCHED)
       builds message_map + counts + overall (FULL_RECOVERY/PARTIAL/...)
  └─ write_report → IMPORT_VERIFICATION_REPORT.json/.html
  └─ fidelity_report → RECOVERY_FIDELITY_REPORT.html, REACTION_RECOVERY_REPORT.html, STICKER_RECOVERY_REPORT.html
```

---

## 6. Reaction reconstruction (Phase B)

```
worker → reconstruction.plan_reactions(src_map[-limit:], mapping, available_sessions, ...)
  ├─ for each archived reaction voter (peer_id): status = SENDABLE | REACTOR_SESSION_REQUIRED
  └─ reconstruction.reconstruct_reactions(client, peer, plan, session_resolver)
       ├─ SENDABLE (this client == reactor): messages.SendReactionRequest  [TELEGRAM]
       └─ other reactor session: resolver→(rx_client,rx_peer); resolve view msg id; send [TELEGRAM]
       └─ never fakes another user's reaction
```

---

## 7. Media reconstruction

Telegram history-import media is bound at upload time (`uploadImportedMedia`) and spliced by
filename into the import. There is NO post-import RPC to attach media to already-created
messages, so media restoration is entirely dependent on the upload step producing a real
MessageMedia (photo/document). Result classification:
- `PHOTO_EXACT` / `STICKER_EXACT` / `AUDIO_EXACT` / ... : real MessageMedia with the type attribute
- `DOCUMENT_ONLY` / `DOCUMENT_EXACT`: generic document (filename/mime only) — NOT the source type
- `MEDIA_ABSENT` / `UNMATCHED`: media not restored