# Architecture

This document describes the runtime architecture of the Telegram Archive & Migration
Suite. The product-level plan and the honest Telegram capability analysis live in
[PROJECT_PLAN.md](../PROJECT_PLAN.md).

## 1. Components

### Backend (FastAPI)

- Application factory in `backend/app/main.py`; routers under `backend/app/api/`.
- Configuration via pydantic-settings (`backend/app/config.py`), overridable with
  environment variables or `.env`.
- Async SQLAlchemy 2.x engine and session factory (`backend/app/database.py`).
- Migrations via Alembic (async env, URL injected from settings).

### Worker (Celery)

- Celery application in `backend/app/workers/celery_app.py` (broker/result backend:
  Redis). Task modules are added in Phases 3–5.
- Long-running tasks use `task_acks_late=True` + DB checkpoints so a killed worker is
  recoverable: re-running the task resumes from the last checkpoint instead of restarting.

### Data stores

| Store | Role |
|---|---|
| PostgreSQL 16 | source of truth: accounts, sessions, export jobs/checkpoints, message & media ledger, audit log |
| Redis 7 | Celery broker + result backend; login-flow state; live progress keys |
| exports/ volume | export artifacts: JSON, SQLite archive, HTML pages, media files, migration packages |

### Frontend

- Vite + React 18 + TypeScript + TailwindCSS; TanStack Query for server state; polling
  for live progress (2s) — robust behind the reverse proxy.
- Built statically and served by its own nginx; the edge nginx routes `/` → frontend,
  `/api` → backend.

### Edge (nginx)

- TLS termination point (certbot-ready), proxies `/api` and `/health`, SPA fallback.
- Exports are only reachable through authenticated API endpoints — nginx never serves
  the exports volume directly.

## 2. Request/Job flows

### Export

```
POST /api/accounts/{id}/exports
  → ChatExport row (queued)
  → Celery: export task
      loop:
        paced fetch batch (Telethon iter_messages, offset_id)   [FloodWait → backoff]
        normalize → stream append to messages.json
        bulk-insert Message rows (batches of 1000)
        enqueue MediaFile rows
        every N messages: checkpoint + poll pause/cancel flags
      media downloads: concurrency-capped, sha256, retry w/ backoff
  → finalize: HTML pages, SQLite archive, stats → completed
```

Crash semantics: checkpoint stores `checkpoint_offset_id` + counts. On resume, iteration
continues from the offset; already-downloaded media is skipped via size+hash match.

### Migration

```
completed ChatExport → POST /api/migrations
  → Celery: converter task
      messages.json → _chat.txt (WhatsApp format) + media/ copy + manifest.json
  → ImportPackage (validation_status=pending)
  → POST /api/import/validate → validation report
  → GET /api/import/{id}/instructions → step-by-step guide
```

## 3. Key design decisions

1. **WhatsApp-format converter is the migration keystone** — Telegram's only official
   history import path accepts WhatsApp/Line/KakaoTalk packages (see README "Known
   Telegram Limitations").
2. **JSON file is the canonical export artifact**; the `messages` table is a queryable
   ledger for stats/validation/search.
3. **Honest progress** — percentage only when the total is known; otherwise counts,
   EMA speed and ETA.
4. **Encrypted sessions at rest** — Telethon session strings are Fernet-encrypted with a
   key from the environment; plaintext never touches disk.
5. **Conservative pacing** — exports default to ≈1 msg/s with flood-wait backoff and
   capped media concurrency to protect accounts from limitation.

## 4. Directory layout

```
backend/app/
  api/        FastAPI routers
  core/       security, audit, crypto (Phase 2)
  models/     SQLAlchemy models (8 tables)
  schemas/    Pydantic schemas (Phase 2+)
  services/   session manager, export engine, downloader, exporters, converter (Phases 2–5)
  workers/    Celery app + tasks
backend/alembic/   migrations
frontend/src/
  pages/      Dashboard, Accounts, Exports, Migration, ImportAssistant, Jobs
  api/        typed client
  components/
scripts/
  db.sh              alembic against the compose Postgres from the host
  db-test-create.sh  (re)create the dedicated test database
  test-pg.sh         pytest against real Postgres
```
