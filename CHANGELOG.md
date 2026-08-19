# Changelog

All notable changes to this project are documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added (planned)

- Phase 3: Chat export engine (checkpointed, pause/resume/cancel, JSON/HTML/SQLite writers).
- Phase 4: Media downloader (concurrency-capped, hashed, retryable).
- Phase 5: WhatsApp-compatible migration builder, test package builder, import assistant.
- Phase 6: React dashboard.
- Phase 7: Full test suite + release.

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
