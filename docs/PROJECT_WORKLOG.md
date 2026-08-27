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

### Root cause (evidence-based)
1. Unit tests prove only text format + InputMedia attribute shape — not real Telegram media binding.
2. Real production imports currently degrade every media item to a generic document, and fail to
   restore photo+caption, albums, stickers, reactions (in the most recent live runs).
3. The uncommitted canonical-media work has never been deployed/live-verified.
4. The parity report "ACHIEVED" is **not supported** by real runtime evidence.

---

## TASKS

### PHASE 1 — Understanding & documentation  (mostly DONE)
- [x] Read entire repo (backend services/api/models/workers, frontend pages, docker, docs)
- [x] docs/PROJECT_CODE_MAP.md
- [x] docs/EXECUTION_FLOW.md
- [x] docs/PRODUCTION_IMPORT_TRACE.md
- [x] docs/TEST_PATH_TRACE.md
- [x] docs/CANONICAL_ARCHIVE_AUDIT.md
- [ ] Root-cause writeup (in-progress) + commit docs

### PHASE 2 — Root cause fix (ONE canonical code path)
- [ ] EXACT reproduction of the media-colapse (%SOURCE fixture vs classify_media bug)
- [ ] Decide: source archive media-type fidelity fix (classify_media / preserve ctor+attrs)
      vs. deploying the uncommitted canonical media service.
- [ ] Ensure production worker uses the SAME serializer + media service + session resolver as the
      verified path (no test-only shortcuts). Confirmed stuck point: running worker is stale.
- [ ] Rebuild & redeploy backend+worker images with the fixes.
- [ ] Run existing test suite to verify nothing regressed.

### PHASE 3 — Real E2E recovery (production path, live Telegram)
Per master prompt §28-§50, marker prefix `RECOVERY_FINAL_20260827_`.
- [ ] Create REAL deterministic fixture covering: plain text, formatted text, photo(+caption),
      sticker, video, gif, audio(+title/performer), document, reply parent/child, reactions (A&B),
      custom emoji, two-photo album, forwarded audio, text adjacent to media.
- [ ] Normal app EXPORT (Account A) → export verification PASS.
- [ ] Normal package builder → package_roundtrip check.
- [ ] B-side clear only (deleteHistory just_clear=true revoke=false) + verify A intact.
- [ ] Normal app IMPORT (Account B) → post-import reconstruction.
- [ ] Real target MTProto read → field-by-field compare.
- [ ] FINAL_RECOVERY_REPORT.html/.json + PRODUCTION_PARITY_REPORT.md with real evidence.
- [ ] Preserve ALL debug artifacts (run_id.txt, source_*, media traces, target snapshots).

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