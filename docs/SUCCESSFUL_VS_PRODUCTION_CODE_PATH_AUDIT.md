# Successful Test vs Production Code Path Audit

**Generated:** 2026-08-27  
**Branch:** max-fidelity-archive

## Executive Summary

There are **critical code path divergences** between the successful isolated tests and the production import pipeline. The production code uses different media markers, different upload sequences, and different serialization formats.

---

## Feature Comparison Table

| Feature | Successful Test File | Successful Function | Production File | Production Function | Same Code Path? | Difference |
|---------|---------------------|---------------------|-----------------|---------------------|-----------------|------------|
| TEXT | `caption_final_test.py` | inline body string | `import_serializer.py` | `build_import_file` | **NO** | Test uses inline format, production uses serializer |
| PHOTO | `matrix_media.py` | `InputMediaUploadedPhoto(file=jf)` | `import_tasks.py` | `_build_input_media` | **PARTIAL** | Same constructor but different file handling |
| PHOTO+CAPTION | `caption_final_test.py` | `<attached: file>\nCAPTION` | `import_serializer.py` | `_media_marker` + continuation | **NO** | Test uses `<attached:>` + caption continuation; production uses same BUT media_count differs |
| VIDEO | `minimal_import_test.py` | `InputMediaUploadedDocument` + attrs | `import_tasks.py` | `_build_input_media` | **YES** | Same attributes |
| GIF | `minimal_import_test.py` | `DocumentAttributeAnimated` | `import_tasks.py` | `_build_input_media` | **YES** | Same attributes |
| AUDIO | `minimal_import_test.py` | `DocumentAttributeAudio` | `import_tasks.py` | `_build_input_media` | **YES** | Same attributes |
| VOICE | `minimal_import_test.py` | `DocumentAttributeAudio(voice=True)` | `import_tasks.py` | `_build_input_media` | **YES** | Same attributes |
| DOCUMENT | `minimal_import_test.py` | `InputMediaUploadedDocument` | `import_tasks.py` | `_build_input_media` | **YES** | Same attributes |
| STICKER | `minimal_import_test.py` | `DocumentAttributeSticker + ImageSize` | `import_tasks.py` | `_build_input_media` | **YES** | Same attributes |
| GROUPED MEDIA | `recovery_e2e.py` | `grouped_id` preservation | `import_tasks.py` | No upload for grouped | **NO** | Production doesn't handle grouped_id upload |
| REACTION | `recovery_e2e_import.py` | `messages.SendReactionRequest` | `reconstruction.py` | `reconstruct_reactions` | **YES** | Same API but multi-session resolution differs |
| REPLY | None successful | N/A | `import_verification.py` | Mapping only | **N/A** | Not supported by Telegram import |
| FORWARD | None successful | N/A | `import_verification.py` | `fwd_from` only | **N/A** | Not supported by Telegram import |

---

## Critical Differences Found

### 1. Media Marker Syntax MISMATCH

| Location | Marker Used | Works? |
|----------|-------------|--------|
| `caption_final_test.py` (SUCCESS) | `<attached: filename>` | ✅ YES |
| `matrix_media.py` (SUCCESS) | `<attached: filename>` | ✅ YES |
| `minimal_import_test.py` (PARTIAL) | `FILE (file attached)` | ❌ NO |
| `import_serializer.py` (PRODUCTION) | `<attached: filename>` | ✅ CORRECT |
| `import_tasks.py` regex (PRODUCTION) | Matches BOTH `<attached:>` and `(file attached)` | ⚠️ ACCEPTS BROKEN FORMAT |

**Root Cause:** The production regex in `import_tasks.py:330-333` accepts the broken `(file attached)` format from old packages, but the serializer correctly emits `<attached:>`. If an old package exists, the wrong marker gets uploaded.

### 2. media_count Calculation

| Location | How Calculated |
|----------|----------------|
| `caption_final_test.py` | `media_count=1` (hardcoded per test) |
| `matrix_media.py` | `media_count=len(files)` |
| `import_tasks.py` (PRODUCTION) | `stats["media_refs"]` from serializer |

**Issue:** The serializer counts `media_refs` as **unique filenames** referenced in the import file. But grouped media (album) has **2 photos with same filename** → only counted once → `media_count` too low → Telegram rejects some media.

### 3. Upload Pipeline Order

**Successful Test (`caption_final_test.py`):**
```python
# 1. Upload import text file
fh = await cb.upload_file(body.encode())
# 2. Init import with media_count
init = await cb(InitHistoryImportRequest(peer, fh, media_count=1))
# 3. Upload media FILE to Telegram
jf = await cb.upload_file(io.BytesIO(JPEG))
# 4. UploadImportedMedia with InputMediaUploadedPhoto
res = await cb(UploadImportedMediaRequest(peer, import_id, 'final.jpg', InputMediaUploadedPhoto(file=jf)))
# 5. Start import
await cb(StartHistoryImportRequest(peer, init.id))
```

**Production (`import_tasks.py`):**
```python
# 1. Build import file (calls serializer)
import_file = export_dir / "import" / "import.txt"
# 2. Check format
importer.check_history_import(import_head)
# 3. Init import
import_id = await importer.init_history_import(peer, import_file, media_count)
# 4. Upload media files + uploadImportedMedia in loop
for filename, info in media_map.items():
    media = await _build_input_media(client, info)
    token = await importer.upload_imported_media(peer, import_id, filename, media)
# 5. Start import
ok = await importer.start_history_import(peer, import_id)
```

**Difference:** Test uploads the import text file FIRST, then media. Production does same. BUT test uses a simple JPEG bytes; production uses canonical archive files.

### 4. Grouped Media (Album) Upload

**Test:** No successful test exists for grouped media upload. The `recovery_e2e.py` captures grouped_id but never uploads.

**Production:** Does NOT handle grouped_id during media upload. Each media in album is uploaded independently without group context.

### 5. Reaction Reconstruction Multi-Session

**Test (`recovery_e2e_import.py:384`):**
```python
await client(functions.messages.SendReactionRequest(...))
```
Uses the SAME client that imported.

**Production (`reconstruction.py:225`):**
```python
await send_client(functions.messages.SendReactionRequest(...))
```
Uses `session_resolver` to find reactor's session. But the mapping from source message ID → target message ID is per-participant (different IDs for A vs B).

---

## Code Path Flow Comparison

### Successful Test Path
```
caption_final_test.py
    → builds body string with <attached:> marker
    → uploads body as import file
    → initHistoryImport(media_count=1)
    → uploads media file to Telegram
    → uploadImportedMedia(InputMediaUploadedPhoto)
    → startHistoryImport
    → polls target for materialization
    → verifies target.message == caption AND target.media != None
```

### Production Path
```
UI: /api/import/start-real
    → import_tasks.run_import(job_id)
    → _run_import_async()
    → build_import_file() → serializer
    → parse_import_head()
    → check_history_import()
    → init_history_import(media_count)
    → media upload loop (_build_input_media + upload_imported_media)
    → start_history_import()
    → verify (import_verification.run_verification)
    → reconstruction.reconstruct_reactions()
    → fidelity reports
```

---

## Files to Consolidate

### Test-only files that should be DEPRECATED or MERGED:
1. `backend/scripts/caption_final_test.py` - Working caption test
2. `backend/scripts/matrix_media.py` - Working media matrix
3. `backend/scripts/minimal_import_test.py` - Working minimal tests
4. `backend/scripts/recovery_e2e_import.py` - Full E2E with reactions
5. `backend/scripts/independent_import.py` - Independent import test
6. `backend/scripts/media_matrix.py` - Media matrix test
7. `backend/scripts/recovery_e2e.py` - E2E recovery
8. `backend/scripts/timestamp_experiment.py` - Timestamp test

### Production files that need FIXING:
1. `backend/app/services/import_serializer.py` - ✅ Already correct (bracket + `<attached:>`)
2. `backend/app/workers/import_tasks.py` - ❌ Regex accepts broken format; media_count for grouped media wrong
3. `backend/app/services/telegram_import.py` - ✅ Correct wrapper
4. `backend/app/services/reconstruction.py` - ⚠️ Multi-session needs verification
5. `backend/app/services/import_verification.py` - ✅ Correct verification

---

## Action Plan

1. **Fix `import_tasks.py` regex** - Only accept `<attached:>` marker, reject `(file attached)`
2. **Fix `media_count` for grouped media** - Count actual media items, not unique filenames
3. **Create `TelegramImportedMediaService`** - Single canonical media upload service
4. **Verify multi-session reaction resolution** - Use `session_resolver` with per-participant IDs
5. **Add media_count assertion** - `declared == uploaded == actual in package`
6. **Remove retry logic that duplicates imports** - Idempotency by import_id