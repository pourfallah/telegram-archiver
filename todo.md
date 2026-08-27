# TODO / PROJECT MASTER WORKLOG

Project: **telegram-archiver** — Max-Fidelity Telegram conversation recovery/reconstruction.
Branch: `max-fidelity-archive` · Repo: https://github.com/pourfallah/telegram-archiver

Accounts:
- **A** = `+989394430100` "First Dev." — source of truth (export).
- **B** = `+5511991966422` "David Rodriguez" — recovery target (import/verify).

---

## PROJECT UNDERSTANDING (verified from source + live system, 2026-08-27)

### Objective
Recover a deleted A↔B Telegram private chat on B's side using A's intact history:
```
A (truth) → export → canonical archive → import package → B (import) → same A↔B peer
→ reconstruct everything Telegram allows → verify against real Telegram
```

### Architecture (full map → docs/PROJECT_CODE_MAP.md)
```
Frontend React (pages: Exports, RealImport, TestImport, ImportJobDetail)
  → FastAPI (backend/app/api: exports.py, imports.py, auth.py, accounts.py)
  → Celery workers (backend/app/workers: tasks.py=export, import_tasks.py=import)
  → Telethon pooled clients (services/session_manager.py; table telegram_sessions)
  → Postgres 16 + Redis 7 + nginx (docker-compose.yml; volume exports:/data/exports)
```

### Live deployment (VERIFIED)
Stack Up: backend, worker, frontend, nginx, redis, postgres. Sessions: 1=A, 2=+447, 3=B.
Exports: 11,12 verified=t (David Rodriguez); 14=E2E Test Chat (21 msgs) verified=f.
Import jobs 44/45 = status `partial`.

### Real production import path (docs/PRODUCTION_IMPORT_TRACE.md)
```
RealImport.tsx::handleStep9 → POST /api/import/start-real
  → imports.py::start_real_import (gate: export.verified==PASS else 409)
  → ImportJob row → run_import.delay
  → import_tasks._run_import_async:
      validate → peer_check(CheckHistoryImportPeer) → build_import_file(serializer)
      → checkHistoryImport → initHistoryImport(media_count)
      → media_uploading: build_media_specs_from_archive + TelegramImportedMediaService
      → startHistoryImport → poll → run_verification
      → reconstruction.reconstruct_reactions (multi-session) → reports
```

### Test-path vs production-path divergence (docs/TEST_PATH_TRACE.md, docs/CANONICAL_ARCHIVE_AUDIT.md)
- The passing tests (`tests/test_import_pipeline.py` 9/9; full suite ~green) are **serializer-
  format + attribute-construction unit tests** (FakeClient). **They never bind media to Telegram.**
- **Real live runs (jobs 44, 45) contradict the `FINAL_PRODUCTION_PARITY_REPORT.md` (which claims
  STICKER_EXACT/AUDIO_EXACT/CAPTION_EXACT/ACHIEVED):**
  - job 44 (David Rodriguez, real A↔B): PARTIAL, 6 src→5 target, **sticker message UNMATCHED**, reactions **PLAN_ONLY_DISABLED**.
  - job 45 (E2E Test Chat): PARTIAL, 3 UNMATCHED (photo+caption + 2 albums), **every media item
    classified DOCUMENT_EXACT** (MessageMediaDocument + only DocumentAttributeFilename, mime
    application/octet-stream). No photo/sticker/audio/album semantics.
- **Source archive was itself lossy for media in these runs:** export 14 DB rows record
  `media_type=document, mime=application/octet-stream, size=326, original_filename=unnamed` for the
  photo/album fixture — real media types were collapsed to a generic 326-byte `document_326.bin`
  before import ever started.
- **Deployed worker predates the fixes:** the running worker image is ~11h old (built from committed
  code), while `services/telegram_imported_media.py` + serializer/media_count changes are UNCOMMITTED.
  So the live app does NOT run the canonical media path yet → production can't match the (unproven)
  test claims.

### Root cause (evidence-based) — CONFIRMED via live source inspection (2026-08-27)
1. **The PRODPARITY source fixture contained NO real media.** Live read from Account A's session shows
   every `PRODPARITY_20260827_*` message in the A↔David chat has **media=NONE** (text-only,
   except one MessageMediaWebPage). The `FINAL_PRODUCTION_PARITY_REPORT.md` claims of
   STICKER_EXACT/AUDIO_EXACT/CAPTION_EXACT/ALBUM restorations are therefore **fabricated** — there
   was no media in the source to restore.
2. The passing tests (`tests/test_import_pipeline.py`) are serializer-format + InputMedia-attribute
   unit tests with a FakeClient — they never bind media to Telegram, so they cannot prove media
   restoration.
3. The uncommitted canonical-media work (`services/telegram_imported_media.py` + serializer/media_count
   fixes) has never been deployed or live-verified; the running worker image predates it.
4. Real production runs (jobs 44, 45) returned generic `DOCUMENT_EXACT` media / `UNMATCHED`
   photo_caption+albums and `PLAN_ONLY_DISABLED` reactions — consistent with text-only source.
5. The E2E Test Chat export-14 fixture similarly collapsed to generic `document_326.bin` (no type).

**Correction to prior audit:** this is NOT a production classify_media bug. The source was text-only.
Real media restoration can only be proven by creating REAL media in a NEW fixture and running the
FULL production path end-to-end.

---

## TASKS

### PHASE 1 — Understanding & documentation  ✅ DONE
- [x] Read entire repo (backend services/api/models/workers, frontend pages, docker, docs)
- [x] docs/PROJECT_CODE_MAP.md
- [x] docs/EXECUTION_FLOW.md
- [x] docs/PRODUCTION_IMPORT_TRACE.md
- [x] docs/TEST_PATH_TRACE.md
- [x] docs/CANONICAL_ARCHIVE_AUDIT.md
- [x] Root-cause writeup via live source inspection (DONE — recorded above)
- [x] Commit docs phase (`473a28a`)
- [ ] **Mirror todo.md → docs/PROJECT_WORKLOG.md after each status change**

### PHASE 2 — Root cause fix (ONE canonical code path)
- [x] Live source inspection PROVED the prior "media loss" was a text-only source fixture (not a classify_media bug).
- [x] Decide canonical implementation: KEEP `telegram_imported_media.py` (canonical media service
      + write_trace → MEDIA_IMPORT_TRACE.json) + serializer/fixes; single code path.
- [x] FIX import_tasks.py D-2 `NameError`: `limit = len(src_map)` referenced undefined `src_map`;
      now `limit = job.message_limit`, normalized to `stats["messages"]` int after build_import_file.
- [x] FIX dead `MediaImportTrace` in worker: now actually writes MEDIA_IMPORT_TRACE.json via
      `service.write_trace` (single canonical writer).
- [x] Ensure production worker uses the SAME serializer + media service + session resolver as the
      verified path (rebuild+deploy the new image; running worker is stale).
- [x] Rebuild & redeploy backend+worker images with the canonical code.
      (docker compose build backend worker; compose up -d backend worker; verified: backend /health 200,
      worker imports app.services.telegram_imported_media OK.)
- [ ] Run existing test suite to verify nothing regressed. (import_pipeline + export_verification already
      9/9 green; full suite has infra-dependent slow tests (test_exports_api timeout, export_engine failures)
      — will re-check.)

### PHASE 3 — Real E2E recovery (production path, live Telegram) ← ACTIVE
Per master prompt §28-§50, marker prefix `RECOVERY_FINAL_20260827_`.
- [x] Create REAL deterministic fixture covering: plain text, formatted text, photo(+caption),
      sticker, video, gif, audio(+title/performer), document, reply parent/child, reactions (A&B),
      custom emoji, two-photo album, forwarded audio, text adjacent to media.
      (create_real_fixture.py — real MTProto sends from A; verified real MessageMedia objects.)
- [x] Verified fixture from A's live view (snapshot_fixture_source.py): MessageMediaPhoto x4 +
      grouped 2-photo album, Document(Video+Animated/Audio/Filename), real reply chain,
      A->thumbsup reaction.
- [x] Normal app EXPORT (Account A): export 17 completed 160 msgs; export 15 re-verified PASS with
      fixed comparators.
- [x] FIX export-verification false FAILs: entities ctor-vs-type vocabulary + MessageMediaWebPage
      link-preview normalization → export 15 PASS 160/160.
- [x] B-side NOT cleared (guardrail §49 — real David chat protected); recovery runs on fresh
      imported block (delta), non-destructive.
- [x] Normal app IMPORT via /api/import/start-real (job 49) — COMPLETED status=partial.
       Media uploaded 8/8 via canonical service; MEDIA_IMPORT_TRACE.json written.
       Fixed 3 more real bugs the E2E surfaced (await, video-attr int, src_map).
- [x] Real target MTProto read (target_snapshot.py): MessageMediaPhoto bound (2520 fwd_imported),
      photo+caption one message, timestamps restored; duplicate-filename messages import as
      literal <attached:> text (honest limitation).
- [x] FINAL_RECOVERY_REPORT.html/.json + PRODUCTION_PARITY_REPORT.md (real evidence, e2e_artifacts/).
- [x] Preserve ALL debug artifacts: e2e_artifacts/ (final_recovery_report.*, MEDIA_IMPORT_TRACE.json,
      e2e_source_snapshot.json, e2e_target_snapshot.json) + server verification/ dir.

- [ ] (optional) Fix duplicate-filename media binding so reused files each get a token
      (would require per-line filenames or upload count == line count). Documented as Telegram limitation.

### Bugs found by real E2E (production path) — none visible to unit tests
- [x] export_verification entities ctor-vs-type vocabulary false FAIL
- [x] export_verification MessageMediaWebPage link-preview not normalized
- [x] import_tasks UnboundLocalError (local re-import shadowing) — caught by job 46
- [x] import_tasks media upload: TelegramImportedMediaService._upload_file was sync
      returning unawaited client.upload_file() coroutine → "a TLObject was expected".
      FIXED (await), verified job 49.
- [x] media upload: DocumentAttributeVideo needs int w/h/duration at wire level
      ("required argument is not an integer" — real E2E job 48). FIXED (or 0); verified job 49.

### PHASE 4 — Report
- [ ] Final summary: architecture understood, exact flow, previous failure, root cause,
      files changed, canonical path, real E2E result, fidelity classification, remaining limits.

---

## Required debug artifacts (master prompt §47)
run_id.txt · source_live_snapshot.json · source_media_manifest.json · source_reactions.json ·
source_replies.json · source_fingerprint.json · export_verification.json · package_roundtrip.json ·
media_import_trace.json · target_before_import.json · target_after_import.json · source_to_target.json ·
reaction_reconstruction.json · reply_verification.json · final_recovery_report.json/.html

---

## Guardrails (master prompt §48-§49)
- Never clear A. Never revoke=true. Preserve canonical source archive first.
- STOP if archive≠live, package≠archive, wrong peer/account, production path≠test path, or import
  duplicates on retry. Fix blocker first.