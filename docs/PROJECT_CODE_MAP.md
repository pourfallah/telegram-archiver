# PROJECT CODE MAP

**Branch:** `max-fidelity-archive`
**Repo:** https://github.com/pourfallah/telegram-archiver
**Date:** 2026-08-27

This map traces the full application: Frontend → API → Service → Worker → Telegram → Database.
It is written from source inspection, not from the README or agent claims.

---

## 1. Frontend (`frontend/src`)

| Page | File | API calls |
|------|------|-----------|
| Login | `pages/Login.tsx` | `POST /api/auth/login` |
| Accounts | `pages/Accounts.tsx` | `GET/POST /api/accounts`, `POST /api/accounts/{id}/login`, `POST /api/accounts/{id}/logout`, `GET /api/accounts/{id}/chat-exports` |
| Dashboard | `pages/Dashboard.tsx` | `GET /api/stats`, `GET /api/health` |
| Exports | `pages/Exports.tsx` | `GET /api/exports`, `POST /api/exports`, `GET/POST /api/exports/{id}/...`, `GET /api/exports/{id}/export` |
| Real Import | `pages/RealImport.tsx` | `GET /api/import/{acct}/target-chats`, `POST /api/import/{acct}/validate-peer`, `POST /api/import/start-real`, `GET /api/import/jobs/{id}` |
| Test Import | `pages/TestImport.tsx` | `POST /api/import/{acct}/test-import` |
| Import Assistant | `pages/ImportAssistant.tsx` | import helpers, format/peer checks |
| Import Job Detail | `pages/ImportJobDetail.tsx` | `GET /api/import/jobs/{id}`, `POST /api/import/jobs/{id}/start` |
| Migration | `pages/Migration.tsx` | migrations API |
| API client | `lib/api.ts`, `lib/types.ts` | axios wrapper; typed DTOs |

---

## 2. API layer (`backend/app/api`)

| Router | File | Endpoints → Service |
|--------|------|---------------------|
| auth | `auth.py` | login → `core/security.py` |
| accounts | `accounts.py` | login/logout/verify → `services/session_manager.py` |
| exports | `exports.py` + `accounts_exports.py` | start/pause/cancel/export → Celery `export.run` → `services/export_engine.py` |
| imports | `imports.py` | target-chats, validate-peer, test-import, start-real, job status → create `ImportJob` row + `run_import.delay()` |
| migrations | `migrations.py` | old migration pipeline (WhatsApp TXT) |
| stats | `stats.py` | aggregate counts |
| health | `health.py` | ping |

---

## 3. Service layer (`backend/app/services`)

| Service | File | Responsibility |
|---------|------|----------------|
| **Export engine** | `export_engine.py` | Fetch history, journal messages, download media, checkpoint, finalize |
| **Telegram utils** | `telegram_utils.py` | `message_to_dict` — the canonical JSON message shape (schema v2); entity/reply/forward/reaction/media serialization |
| **Media downloader** | `media_downloader.py` | Download media for messages |
| **Export writers** | `export_writers.py` | messages.jsonl / messages.json / sqlite / html |
| **Canonical archive** | `canonical_archive.py` | Build `archive/` (messages.ndjson, media/, manifest, chat.json, participants, checksums) |
| **Export verification** | `export_verification.py` | `verify_export` — live source vs canonical archive per-field; gates import |
| **Import serializer** | `import_serializer.py` | `build_import_file` — produce Telegram import.txt (bracket ts + `<attached:>` marker + caption continuation) |
| **Telegram importer** | `telegram_import.py` | `checkHistoryImportPeer`, `checkHistoryImport`, `initHistoryImport`, `uploadImportedMedia`, `startHistoryImport` |
| **Imported media** | `telegram_imported_media.py` (NEW) | Canonical media upload service (InputMedia build + rich attributes + trace) |
| **Import verification** | `import_verification.py` | `run_verification` — source archive vs imported target, honest classification |
| **Reconstruction** | `reconstruction.py` | `plan_reactions` / `reconstruct_reactions` — identity-correct reaction Phase B; reply/sticker = archival-only |
| **Fidelity reports** | `fidelity_report.py`, `fidelity_reports.py` | HTML reports: recovery, reaction, sticker |
| **Session manager** | `session_manager.py` | Acquire/release `TelegramClient` per account, pooled |
| **Task runner** | `task_runner.py` | Celery wrappers |

---

## 4. Worker (background) (`backend/app/workers`)

| Worker | File | Job |
|--------|------|-----|
| Export | `tasks.py` | `export.run` → `ExportEngine.run(export_id)` |
| Import | `import_tasks.py` | `run_import` → `_run_import_async` — full protocol: validate → build file → checkHistoryImport → initHistoryImport → uploadImportedMedia → startHistoryImport → wait → verify → reconstruct reactions → reports |

Celery: `celery_app.py`; executes via `docker compose` worker container (concurrency=1, rate-limited).

---

## 5. Execution chain (import)

```
RealImport.tsx::handleStep9
  └─ POST /api/import/start-real
       └─ imports.py::start_real_import
            ├─ gate: export.verified must be PASS (409 otherwise)
            ├─ build_canonical_archive if missing
            ├─ create ImportJob(row) + commit
            └─ run_import.delay(job.id)
                 └─ worker: import_tasks.run_import
                      └─ _run_import_async
                           ├─ acquire client (target account)
                           ├─ resolve peer
                           ├─ check_history_import_peer
                           ├─ build_import_file (serializer)  → import.txt
                           ├─ check_history_import
                           ├─ init_history_import(media_count)
                           ├─ [media] build_media_specs_from_archive + service.upload_imported_media
                           ├─ snapshot target BEFORE (delta)
                           ├─ start_history_import
                           ├─ poll materialization
                           ├─ run_verification
                           ├─ reconstruction.reconstruct_reactions (multi-session)
                           └─ write reports + debug log
```

---

## 6. Database models (`backend/app/models`)

| Model | Table | Key fields |
|-------|-------|-----------|
| TelegramSession | `telegram_sessions` | phone, api_id, api_hash_encrypted, session_encrypted, status, last_error |
| UserAccount | `user_accounts` | admin creds |
| ChatExport | `chat_exports` | chat_id/title/type, status, verified, verification(JSON), export_dir, messages_processed, files_*, checkpoint |
| Message | `messages` | message_id, grouped_id, date, edit_date, sender_*, text, entities, reply_to_message_id, forwarded_from, reactions, views, media_count, media_types |
| MediaFile | `media_files` | message_id, media_type, mime_type, size_bytes, original_filename, status, extra |
| ImportJob | `import_jobs` | source_export_id, target_account_id, target_peer_id, message_limit, status, options, progress, import_id, error |
| ImportPackage | `import_package` | legacy package |
| MigrationJob | `migration_jobs` | legacy WhatsApp migration |
| AuditLog | `audit_log` | audit events |