# Changelog

All notable changes to this project are documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added (planned)

- Phase 7: Full test suite + release.

## [0.6.0] - 2026-08-19

### Added — Phase 6 (web dashboard)

- Functional React + TypeScript + Tailwind dashboard: JWT login, layout + nav.
- Dashboard cards (accounts, exports, running jobs, storage) via `/api/stats`.
- Accounts page: list, multi-step login wizard (phone → OTP → 2FA), status badges,
  check + delete.
- Exports page: chat search (username/title/id), format picker, create export, live
  progress bars with counts / speed / ETA (3s polling).
- Migration page: convert a completed export to a WhatsApp package, test-package
  builder (10/50/100/500/1000), recent migrations + packages list.
- Import Assistant page: select a package, validate (messages/media/users/date range),
  and view step-by-step import instructions.
- Production `vite build` passes cleanly.

## [0.5.0] - 2026-08-19

### Added — Phase 5 (converter + import assistant)

- WhatsApp-compatible migration builder (`services/converter.py`): converts a Telegram
  export into a `_chat.txt` + `media/` + `manifest.json` package that Telegram's official
  importer accepts. Line format `DD/MM/YYYY, HH:mm - Sender: message`, `<Attached: file>`
  + caption lines, and a **strict sender map** (distinct sender ids are never merged).
- Test migration builder (`services/test_builder.py`): generates 10/50/100/500/1000-message
  packages with real media samples (guarantees the importer's "must contain media" rule).
- Import assistant (`services/import_assistant.py`): validates package structure and
  produces honest, step-by-step import instructions (official-importer path + manual
  fallback), persisted as `INSTRUCTIONS.md`.
- Migration + import APIs: convert a completed export, list migrations, build test
  packages, validate, and fetch instructions.
- Test coverage for converter line-format/sender mapping, test builder, import validation,
  and the full migration API flow (68 tests green).

## [0.4.0] - 2026-08-19

### Added — Phase 4 (media downloader)

- Media downloader (`services/media_downloader.py`): downloads each message's media
  to `media/<type>/<filename>` while streaming SHA-256, records path + hash + status on
  the `MediaFile` ledger, concurrency-capped (`media_concurrency`), bounded retries,
  permanent failures marked for the retry endpoint.
- Inline integration with the export engine: media is downloaded per batch as the
  export streams; `files_downloaded` is updated live.
- Test coverage for on-disk media output + hashed ledger rows (61 tests green).

## [0.3.0] - 2026-08-19

### Added — Phase 3 (export engine)

- Chat export engine (`services/export_engine.py`): paced, checkpointed iteration over
  chat history with flood-wait backoff and bounded retries.
- **Durable checkpointing:** message ledger and offset/count checkpoint commit in the
  same transaction, so a crash or worker loss never re-processes committed messages on
  resume (crash-resume is exact, no duplicates).
- Live progress metrics: messages processed, total estimate, speed (EMA), ETA, percent
  when the total is known. Cooperative pause / resume / cancel.
- Three writers (`services/export_writers.py`): streaming `messages.jsonl` workfile,
  canonical oldest-first `messages.json`, incremental `database.sqlite`, and paged
  browseable HTML (`index.html` + `pages/`).
- Media ledger: `MediaFile` rows (type, MIME, size, filename) queued per message for the
  Phase 4 downloader.
- Export APIs: chat search + export creation (account-scoped), list/detail, progress,
  pause/resume/cancel (terminal-state guards), authenticated file listing + download with
  path-traversal protection, and purge-on-delete.
- Celery `export.run` task + in-process `InlineTaskRunner` for hermetic tests.
- Test suite: export-engine round-trip, crash-resume-no-duplicates, pause/resume, cancel,
  flood-wait recovery, speed/ETA math, and full exports API coverage — **60 tests green**
  on SQLite (PostgreSQL via `TEST_DATABASE_URL`).

## [0.2.0] - 2026-08-19

### Added

- Telegram account authentication flow (phone → OTP → 2FA) via Telethon, with
  a Redis-mirrored login-flow manager and a bounded client pool
  (`services/session_manager.py`).
- Fernet-encrypted session storage (`api_hash` and Telethon session string are
  never stored in plaintext) + Argon2 password hashing + JWT dashboard auth.
- Account management API: create / list / get / status-check / delete,
  scoped per dashboard user.
- Fixed-window Redis rate limiting on login/code submission (fail-open).
- Audit logging middleware with sensitive-field redaction (`core/audit.py`).
- Admin user automatically seeded at startup from environment variables.
- Test suite: 47 tests (crypto, auth API, account login flows against a mocked
  Telethon client) — green on SQLite and PostgreSQL 16.

## [0.1.0] - 2026-08-19

### Added

- Project plan & architecture review (`PROJECT_PLAN.md`).
- Repository scaffold: `.gitignore`, MIT `LICENSE`, initial `README.md`, `CHANGELOG.md`.
- Backend scaffold: FastAPI application factory, pydantic-settings configuration,
  async SQLAlchemy 2.x engine, health + stats endpoints.
- Data models: `UserAccount`, `TelegramSession`, `ChatExport`, `Message`, `MediaFile`,
  `MigrationJob`, `ImportPackage`, `AuditLog`.
- Alembic async migration setup with initial schema migration.
- Celery application placeholder (broker wiring for Phase 3 tasks).
- Frontend scaffold: Vite + React 18 + TypeScript + TailwindCSS, multi-stage Docker build.
- Docker Compose stack: postgres, redis, backend, worker, frontend, nginx reverse proxy.
- CI workflow (lint + tests + frontend build) ready for GitHub.
- Initial documentation: `docs/architecture.md`, `docs/deployment.md`, `docs/api.md`, `docs/development.md`.
