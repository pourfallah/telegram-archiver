# PRODUCTION IMPORT TRACE

Exact production path when the user presses **START REAL IMPORT** (button → Telegram → verify).

## Frontend
`frontend/src/pages/RealImport.tsx`
- `handleStep9()` (line ~208) — triggered by "Start Real Import" button (case 9)
- `handleStep4()` -> `POST /api/import/{targetAccountId}/validate-peer` (peer validation)
- calls `post('/api/import/start-real', {...})`

## API
`backend/app/api/imports.py`
- `start_real_import(payload: StartImportRequest)` (line ~298)
  - gate: `export.verified` and `export.verification.status == "PASS"` else `409 export_not_verified`
  - `build_canonical_archive(export_dir, archive_dir, ...)` if missing
  - create `ImportJob(source_export_id, target_account_id, target_peer_id, message_limit, options={contact_identifier, test_mode:False})`
  - `run_import.delay(job.id)`  ← dispatches Celery task
- schema: `backend/app/schemas/import_job.py` → `StartImportRequest`, `ImportJobPublic`

## Worker
`backend/app/workers/import_tasks.py`
- `run_import(job_id)` (Celery task, line ~128) → `asyncio.run(_go())`
- `_run_import_async(job_id, local_factory)` (line ~162) — the whole pipeline:

```
acquire target client        session_manager.SessionManager.acquire_client(account)
resolve peer                 client.get_entity(target_peer_id) -> get_input_entity
Phase peer_checking          importer.check_history_import_peer(peer)      [TELEGRAM]
Phase building_import_file   import_serializer.build_import_file(export_dir, import.txt, limit, tz)
Phase check_import_format    importer.check_history_import(import_head)    [TELEGRAM]
Phase init_history_import    importer.init_history_import(peer, file, media_count)  [TELEGRAM]
Phase media_uploading        telegram_imported_media.build_media_specs_from_archive(...)
                             TelegramImportedMediaService(client).upload_imported_media(peer, import_id, spec)
Phase starting_import        importer.start_history_import(peer, import_id) [TELEGRAM]
Phase verifying              import_verification.run_verification(archive, new_target_dicts)
Phase reconstruction         reconstruction.plan_reactions/reconstruct_reactions
```

## Telegram wrapper
`backend/app/services/telegram_import.py`
- `TelegramImporter` class
  - `check_history_import_peer` → `messages.CheckHistoryImportPeerRequest`
  - `check_history_import` → `messages.CheckHistoryImportRequest`
  - `init_history_import` → `messages.InitHistoryImportRequest(peer, file, media_count)` → `import_id`
  - `upload_imported_media` → `messages.UploadImportedMediaRequest(peer, import_id, file_name, media)`
  - `start_history_import` → `messages.StartHistoryImportRequest(peer, import_id)`

## Serializer
`backend/app/services/import_serializer.py`
- `build_import_file(export_dir, out_file, limit, sender_map, tz_offset_minutes)`
- `_format_ts` → `[DD/MM/YYYY, HH:MM:SS]` (bracket, seconds — verified)
- `_media_marker(filename, media_type)` → `<attached: FILENAME>`
- one source message = one block: `[ts] - Name: <attached: file>\n<caption>` or `[ts] - Name: text`
- `parse_import_head(file)` → first 100 lines

## Canonical Media Service
`backend/app/services/telegram_imported_media.py` (NEW, currently uncommitted)
- `build_media_specs_from_archive(export_dir, import_text, limit)` → `list[MediaUploadSpec]`
  (extracts `<attached: FILE>` filenames, loads archive media files + rich metadata)
- `TelegramImportedMediaService(client)`
  - `build_input_media(spec)` → `InputMediaUploadedPhoto` / `InputMediaUploadedDocument(+attributes)`
  - `upload_imported_media(peer, import_id, spec)` → `messages.UploadImportedMediaRequest`
  - `_build_document_attributes`: sticker→`DocumentAttributeSticker`+ImageSize, gif→`Animated`,
    video→`DocumentAttributeVideo`, audio→`DocumentAttributeAudio`, voice→`DocumentAttributeAudio(voice=True)`
  - writes `MEDIA_IMPORT_TRACE.json` (source_message→filename→ctor→doc/photo id)

## Post-import reconstruction
`backend/app/services/reconstruction.py`
- `plan_reactions(src, mapping, session_account_ids, source_me_id, target_me_id)` — strict identity
- `reconstruct_reactions(client, peer, plan, new_target_ids, session_resolver)` — `messages.SendReactionRequest`

## Verification
`backend/app/services/import_verification.py`
- `run_verification(source_archive_dir, new_target_dicts, imported_count)` → report
- `ImportVerification.compare()` → per-field honest classification
- `write_report` → `IMPORT_VERIFICATION_REPORT.json/.html`

## Reports
- `fidelity_report.build_fidelity_report` → `RECOVERY_FIDELITY_REPORT.html`
- `fidelity_reports.build_fidelity_report / build_reaction_report / build_reaction_recovery_report / build_sticker_recovery_report`
- `IMPORT_DEBUG_LOG.json` (job reproducibility record)

## DB state transitions (import_jobs)
`queued → validating → peer_checking → import_initialized → media_uploading → starting_import → verifying → completed | partial | failed`
- `import_id` persisted (returned by initHistoryImport)
- `progress` JSON updated each phase