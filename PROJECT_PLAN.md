# Telegram Archive & Migration Suite — Project Plan

> **Status:** DRAFT v0.1.0 — awaiting architecture review & approval
> **Scope:** full product plan; no application code is written before approval

---

## 1. Executive Summary

We are building **Telegram Archive & Migration Suite**, a self-hosted, production-grade
application that:

1. Logs in one or more Telegram user accounts (OTP + 2FA), storing **encrypted** sessions.
2. Exports full chat history (messages, metadata, media) to **JSON / HTML / SQLite** with
   real progress, pause/resume/cancel, checkpointing and retry.
3. Converts exports into **WhatsApp-compatible import packages** (`_chat.txt` + media),
   which is the *only* format Telegram's official importer accepts for restoring history
   into a fresh account.
4. Provides a **test package builder**, an **import assistant wizard**, and a modern
   **React dashboard** with live job progress.

Everything is honest about what Telegram's API can and cannot do (Section 2). We do not
ship features that Telegram's platform makes impossible.

---

## 2. Telegram API Reality Check (read first)

This section drives every architectural decision in this document.

### 2.1 What IS technically possible (MTProto / Telethon)

| Capability | Notes |
|---|---|
| Full history read of any chat the account is a member of | Private chats, groups, channels the account has joined. `iter_messages` can page through the entire accessible history. |
| Rich message metadata | id, date, edit date, sender id/name/username, text, entities (bold/italic/link/mention...), reply-to, forward origin, reactions, channel views, service messages. |
| Media download | Photos, videos, documents, voice, audio, stickers, GIFs/animated, with original filename, MIME type and size. |
| Multiple accounts / sessions | MTProto allows several simultaneous user sessions; we manage them in a pool. |
| Dialog search | By username, title, or chat id; phone-based lookup works for contacts in some cases (limited — see 2.3). |
| Saved Messages export | A normal dialog with yourself; fully exportable. |

### 2.2 What is NOT possible — hard platform limits (we will NOT fake these)

1. **No arbitrary history import into Telegram via API.** There is no public API to
   backfill/restore messages into a chat, no "post as another user", no server-side
   history restore for user accounts. Telegram's **only** official import path is the
   desktop feature *Settings → Advanced → Import from...* (backed by Telegram's import
   bot), and it accepts **only WhatsApp, Line, and KakaoTalk** export packages.

2. **Consequence — the migration strategy.** The only legitimate "move history back into
   Telegram" flow is:
   `Telegram export → convert to WhatsApp-style package → official Telegram importer`.
   That is exactly what our converter produces. Caveats we document and surface in the
   UI:
   - Telegram's importer is designed for WhatsApp exports; **messages without attached
     media may be skipped** by the importer (we verify against current importer behavior
     and say so in the Import Assistant).
   - Reactions, edits, view counts, reply linkage and forward provenance are **not**
     preserved by the importer — imported chats are plain messages, sender attribution
     preserved only as text.
   - The importer must receive at least one media file to accept a package (our test
     builder guarantees this).
   - Import target must be a **fresh account** / empty chat for the official flow.
   - A **manual fallback** (re-share media via the app) is always offered as an option.

3. **No access to chats the account cannot see.** Private groups/channels the account
   was removed from or never joined, and **secret chats** (E2E, never stored server-side),
   cannot be exported. Deleted messages are gone; accounts that lost access lose history.

4. **No message-level "who deleted what" or server-side search APIs** beyond what the
   client can page through.

### 2.3 Soft limits & operational risks (managed at runtime)

| Risk | Mitigation in this product |
|---|---|
| `FloodWaitError` and dynamic rate limits | Paced iteration (configurable msgs/sec, conservative default), automatic flood-wait sleep with backoff, concurrency caps on downloads. |
| Account limitation / temporary ban from aggressive automation | Conservative defaults, per-account pacing, prominent warnings in docs + UI, never parallel-export more than N chats per account (config). |
| Phone-number search rarely resolves non-contacts | Search by username / chat id / title is primary; phone search documented as best-effort. |
| Total message count is only an estimate | Use `get_messages(chat, 0).total` when available; percentage shown when total known, otherwise counts + ETA only (honest progress). |
| Long-running tasks killed by infra | Checkpoint-based resume (Section 9.2); worker loss is recoverable by re-running from checkpoint. |
| Telegram TOS | Tool is for archiving your own accounts/chats. Docs carry a responsible-use section. |

### 2.4 Design consequences

- The **WhatsApp converter is the keystone** migration feature — not a "Telegram → Telegram
  import" (which is impossible).
- The **Import Assistant** generates truthful instructions: official-importer path when
  applicable, manual fallback otherwise, and clearly states what will/won't be preserved.
- Export engine is built for **hours-long runs** with checkpointing as a first-class design,
  not an afterthought.

---

## 3. Goals & Non-Goals

**Goals**
- Production-grade, containerized, self-hosted product (not a script).
- Multi-account encrypted Telegram sessions; robust export with pause/resume/cancel/retry;
  JSON + HTML + SQLite outputs; WhatsApp-compatible migration packages; test builder;
  import assistant; dashboard; tests; full docs.

**Non-goals (deliberate)**
- Telegram→Telegram automatic history import (impossible — see 2.2).
- Exporting secret chats or chats the account cannot access.
- Bypassing rate limits, bans, or Telegram ToS.
- Multi-tenant SaaS features; this is a single-admin self-hosted appliance.

---

## 4. Architecture

### 4.1 Component diagram

```
                    ┌──────────────────────────────┐
   Browser (React)  │           nginx              │
   SPA: Dashboard,  │  TLS termination, /api proxy │
   Accounts,        └──────┬───────────────┬───────┘
   Exports, Jobs,          │ /             │ /api
   Migration wizard        ▼               ▼
                    ┌──────────┐    ┌──────────────┐
                    │ Frontend │    │   FastAPI    │
                    │ (built   │    │  (uvicorn)   │  REST + JWT + audit
                    │  assets) │    └──┬───────┬───┘
                    └──────────┘       │       │ SQL / Redis
                                       │       ▼
                    ┌──────────────────┼──┐  ┌──────────┐  ┌──────────┐
                    │  Celery worker   │  │  │PostgreSQL│  │  Redis   │
                    │  export engine,  │  │  │ (models, │  │ (broker, │
                    │  media download, │  │  │  ledger, │  │  progress│
                    │  converter,      │  │  │  audit)  │  │  cache)  │
                    │  validator       │  │  └──────────┘  └──────────┘
                    └──────────┬───────┘  │
                               │          │
                    ┌──────────▼──────────▼────────┐
                    │      exports/ (shared volume) │
                    │  exports/<account>/<chat>/…   │
                    └──────────┬───────────────────┘
                               │ MTProto (Telethon), encrypted sessions
                    ┌──────────▼───────────────────┐
                    │        Telegram servers       │
                    └───────────────────────────────┘
```

### 4.2 Process model

- **FastAPI** serves REST; auth via JWT; audit middleware logs mutating calls.
- **Celery worker** (prefork, concurrency configurable) executes long-running jobs:
  export, media retry, conversion, validation. Export tasks use `asyncio.run` internally
  (Telethon is async); `acks_late` + DB checkpointing make worker loss recoverable.
- **Redis** = Celery broker + result backend + login-flow state + live progress keys.
- **PostgreSQL** = source of truth for models, message/media ledger, audit log.
- **Shared `exports/` volume** mounted by API and worker; API streams files to the browser
  (authenticated), so nginx never needs direct filesystem access.

### 4.3 Data flow — export

```
POST /exports {account_id, chat, format}
  → ChatExport row (status=queued)
  → Celery task export.run
      loop:
        fetch batch (paced) → normalize → bulk-insert Message rows
        stream to messages.json (append) → queue media
        every 250 msgs: checkpoint (offset_id, counts, speed, ETA) + poll pause/cancel
      media worker: download w/ concurrency cap → hash → MediaFile rows → files on disk
  → finalize: stats, HTML pages, SQLite archive, status=completed
```

### 4.4 Data flow — migration

```
ChatExport completed → POST /migrations {export_id}
  → MigrationJob → converter task:
      parse messages.json → _chat.txt (WhatsApp format) + media/ copy + manifest.json
  → ImportPackage row (validation pending)
  → Import Assistant: validate → stats (messages/media/users/date range) → instructions
```

---

## 5. Tech Stack & Key Dependencies

| Layer | Choice |
|---|---|
| Backend | Python 3.12, FastAPI, Uvicorn, Pydantic v2, pydantic-settings |
| Telegram | Telethon (MTProto) |
| ORM / DB | SQLAlchemy 2.x (async), Alembic, PostgreSQL 16 |
| Queue | Celery, Redis 7 (broker + results + progress) |
| Crypto | `cryptography` (Fernet for session-at-rest), argon2 (password hashing) |
| Auth | JWT (PyJWT), HTTPBearer; single admin user seeded from env |
| Tests | pytest, pytest-asyncio, httpx, fakeredis, testcontainers-postgres (optional) |
| Frontend | React 18 + TypeScript + Vite, TailwindCSS, TanStack Query, react-router |
| Infra | Docker Compose (postgres, redis, backend, worker, frontend, nginx), Ubuntu 24.04 |

---

## 6. Repository Layout

```
telegram-archiver/
├── PROJECT_PLAN.md            ← this document
├── README.md                  ← grows every phase
├── CHANGELOG.md               ← grows every phase
├── LICENSE                    (MIT)
├── .env.example               ← all secrets documented, never committed
├── .gitignore
├── docker-compose.yml
├── nginx/
│   └── default.conf
├── backend/
│   ├── Dockerfile
│   ├── pyproject.toml
│   ├── alembic/
│   ├── app/
│   │   ├── main.py            # FastAPI app factory
│   │   ├── config.py          # pydantic-settings
│   │   ├── database.py
│   │   ├── models/            # SQLAlchemy models
│   │   ├── schemas/           # Pydantic schemas
│   │   ├── api/               # routers: auth, accounts, chats, exports,
│   │   │                      #   migrations, import, jobs, stats, audit
│   │   ├── core/              # security, audit log, rate limiting
│   │   ├── services/          # session manager, export engine, downloader,
│   │   │                      #   exporters (json/html/sqlite), converter,
│   │   │                      #   validator, test builder
│   │   └── workers/           # Celery app + tasks
│   └── tests/                 # unit + integration
├── frontend/
│   ├── Dockerfile             # multi-stage: build → nginx static
│   ├── package.json
│   └── src/
│       ├── pages/             # Dashboard, Accounts, Exports, Migration,
│       │                      #   ImportAssistant, Jobs
│       ├── api/               # typed API client
│       └── components/
├── docs/
│   ├── architecture.md
│   ├── api.md
│   ├── deployment.md
│   └── development.md
└── exports/                   # runtime volume (gitignored)
```

---

## 7. Database Design (SQLAlchemy + Alembic)

All models in `app/models/`. Timestamps UTC. Soft-delete not needed; hard delete with
cascade for exports.

### 7.1 `UserAccount` — dashboard users (authentication)
`id, email (unique), password_hash (argon2), is_active, is_admin, created_at, last_login_at`

### 7.2 `TelegramSession` — one row per Telegram account
`id, user_account_id (FK), phone (unique per user), api_id, api_hash_encrypted,
session_encrypted (Fernet), status (new|auth_pending_code|auth_pending_2fa|active|limited|banned|error),
last_error, last_checked_at, created_at, updated_at`
Index: `(user_account_id, status)`.

### 7.3 `ChatExport` — export job + checkpoint state
`id, telegram_session_id (FK), chat_id, chat_title, chat_type (private|group|channel),
format (json|html|sqlite|all), status (queued|running|paused|cancelled|failed|completed),
messages_processed, total_messages_est, files_downloaded, files_total, speed_mps (float),
eta_seconds, checkpoint_offset_id, checkpoint_updated_at, options (JSON), export_dir,
error, started_at, finished_at, created_at`
Indexes: `(telegram_session_id, status)`, `(status)`.

### 7.4 `Message` — export ledger (searchable index; JSON file is the canonical artifact)
`id, chat_export_id (FK), message_id, date, edit_date, sender_id, sender_name,
sender_username, text, entities (JSON), reply_to_message_id, forwarded_from (JSON),
reactions (JSON), views, media_count, media_types (JSON)`
Unique: `(chat_export_id, message_id)`. Index: `(chat_export_id, date)`.
Bulk-inserted in batches of 1000 (`executemany`) — supports millions of rows.

### 7.5 `MediaFile` — per-file download state
`id, chat_export_id (FK), message_id, media_type (photo|video|document|voice|audio|sticker|gif|animation),
mime_type, size_bytes, original_filename, file_path, sha256, status (pending|downloading|downloaded|failed),
attempts, error`
Index: `(chat_export_id, status)`. Unique: `(chat_export_id, message_id, media_type, original_filename)`.

### 7.6 `MigrationJob` — conversion job state
`id, chat_export_id (FK), format (whatsapp), status (queued|running|completed|failed),
messages_converted, media_copied, output_dir, error, created_at, finished_at`

### 7.7 `ImportPackage` — generated/validated packages
`id, migration_job_id (FK), name, package_path, format, messages_count, media_count,
users_detected (JSON), date_min, date_max, validation_status (pending|valid|invalid|warnings),
validation_report (JSON), created_at`

### 7.8 `AuditLog` — security requirement
`id, user_account_id, action, resource_type, resource_id, detail (JSON), ip, created_at`
Index: `(created_at)`.

---

## 8. API Design (summary — full spec in docs/api.md)

```
POST /api/auth/login                    → JWT (admin)
GET  /api/stats                         → dashboard cards (accounts, exports, storage, running jobs)
GET  /api/accounts                      → list Telegram accounts
POST /api/accounts                      → start login (phone, api_id, api_hash) → flow_id
POST /api/accounts/{id}/code            → submit OTP
POST /api/accounts/{id}/2fa             → submit 2FA password
POST /api/accounts/{id}/check           → status check (active/limited/banned)
DELETE /api/accounts/{id}
GET  /api/accounts/{id}/chats?q=        → dialog search (username/title/id)
POST /api/accounts/{id}/exports         → create export {chat, format, options}
GET  /api/exports, GET /api/exports/{id}
POST /api/exports/{id}/pause|resume|cancel|retry-failed
GET  /api/exports/{id}/progress         → %, counts, speed, ETA, logs
GET  /api/exports/{id}/files?path=      → authenticated file download
DELETE /api/exports/{id}                → purge export + files
POST /api/migrations                    → {export_id} → build WhatsApp package
GET  /api/migrations, GET /api/migrations/{id}
POST /api/migrations/test               → {count: 10|50|100|500|1000} test package
POST /api/import/validate               → {package_id} → validation report
GET  /api/import/{package_id}/instructions → step-by-step import guide
GET  /api/jobs                          → live job list w/ progress + logs
GET  /api/audit                         → audit trail (admin)
```

---

## 9. Core Service Designs

### 9.1 Session manager & login flow (`services/session_manager.py`)

- `AuthFlowManager`: in-memory registry of in-progress logins, state mirrored to Redis
  (TTL 10 min) so API restarts don't strand a flow. Flow: `phone → code → (2FA) → done`.
- On success: Telethon `StringSession` → **Fernet-encrypted** with `SESSION_ENCRYPTION_KEY`
  (env) → stored in `TelegramSession.session_encrypted`. Plaintext sessions never touch disk.
- `AccountClientPool`: LRU pool of live `TelethonClient` wrappers (max concurrent sessions
  configurable, default 5). Exports acquire a client; pool reconnects on disconnect and
  checks account status (`get_me`, limitation flags).
- Multiple accounts fully supported; each export binds to one account.

### 9.2 Export engine (`services/export_engine.py`)

- **Iteration:** newest→oldest via `iter_messages(chat, offset_id=…)`, paced to a
  configurable rate (default 1 msg/s, burst 5; env-tunable). Flood-wait → auto-sleep with
  backoff, logged.
- **Checkpoint:** every `CHECKPOINT_EVERY=250` messages persist `checkpoint_offset_id`,
  counts, speed (EMA), ETA to the `ChatExport` row + Redis. **Crash/worker loss ⇒ resume
  task re-runs from checkpoint**; already-downloaded media is skipped (size+hash match).
- **Pause/Resume/Cancel:** cooperative — flag polled at each checkpoint; pause stops after
  current batch, resume spawns a new task from checkpoint, cancel marks terminal and stops.
- **Progress honesty:** if `total` is known (from `get_messages(chat,0).total`), percent =
  processed/total; otherwise indeterminate bar + counts + ETA. Speed = msg/s EMA; ETA =
  remaining/speed.
- **Retry:** failed media rows get `attempts`; `POST /exports/{id}/retry-failed` re-queues
  them; per-file 3 attempts with exponential backoff inside the run.
- **Writers:**
  - `messages.json` — streamed, append per batch, schema in Section 9.4.
  - `database.sqlite` — portable single-file archive built at finalize from the JSON
    (messages + media tables, mirror of the JSON).
  - `index.html` + `pages/page-NNNNN.html` (5000 msgs/page) + media links — tg-archive-style
    browseable export.

### 9.3 Media downloader (`services/media_downloader.py`)

- Queue fed by the engine; **concurrency cap** (default 2, env-tunable); sequential per
  account to stay conservative.
- Streaming download → streaming **SHA-256** → sanitized original filename
  (collision-safe: `name_1.ext`) → `media/{photos|videos|documents|voice|audio|stickers|gifs}/…`
- Skip logic on resume: file exists + size match + hash match ⇒ counted as done, not re-downloaded.

### 9.4 Export JSON schema (canonical artifact)

```json
{
  "schema_version": 1,
  "exported_at": "2026-08-19T12:00:00Z",
  "account": {"phone": "+491234", "username": "me"},
  "chat": {"id": -100123, "title": "Family", "type": "group", "username": null},
  "messages": [
    {
      "id": 42,
      "date": "2026-08-01T09:30:00Z",
      "edited": null,
      "sender": {"id": 1, "name": "Alice", "username": "alice"},
      "text": "Hello \u00e2\u0080\u00a6",
      "entities": [{"type": "bold", "offset": 0, "length": 5}],
      "reply_to": 40,
      "forwarded_from": {"id": 99, "name": "News Channel"},
      "reactions": {"\u2764": 3},
      "views": null,
      "media": [{"type": "photo", "path": "media/photos/photo_1.jpg",
                  "mime": "image/jpeg", "size": 12345, "sha256": "ab12…"}]
    }
  ],
  "stats": {"messages": 1, "media": 1, "first_date": "…", "last_date": "…"}
}
```

### 9.5 WhatsApp-compatible converter (`services/converter.py`)

- Input: export archive (JSON + media). Output:
  ```
  package/
    _chat.txt
    media/…
    manifest.json   # counts, users, date range, schema info
  ```
- Line format (exactly as WhatsApp exports):
  ```
  DD/MM/YYYY, HH:mm - Sender: message text
  DD/MM/YYYY, HH:mm - Sender: <Attached: photo_001.jpg>
  DD/MM/YYYY, HH:mm - Sender: caption text
  ```
- **Sender mapping is strict:** each distinct `sender_id` → exactly one display name
  (resolved from sender name, falling back to username, then phone-hash placeholder).
  **Users are never merged** — distinct sender ids always produce distinct display names,
  verified by an assertion + test.
- Attachments: one `<Attached: file>` line per media file + caption as following line —
  mirroring real WhatsApp exports so Telegram's official importer sees a familiar shape.

### 9.6 Test migration builder (`services/test_builder.py`)

- Generates packages for **10 / 50 / 100 / 500 / 1000** messages with realistic senders,
  timestamps spanning days, text, captions, and **real media samples** (tiny valid
  PNG/JPG/GIF/WAV/WebP fixtures shipped under `backend/tests/fixtures/media/`), including
  at least one sticker-style WebP — guaranteeing the package satisfies Telegram's
  importer's "must contain media" rule.

### 9.7 Import assistant (`services/import_assistant.py`)

- Wizard API: select package → validate (structure, `_chat.txt` parse, media presence,
  manifest consistency) → stats (messages, media, users, date range) → instructions:
  1. Official path: Telegram Desktop → Settings → Advanced → *Import from WhatsApp*,
     target = fresh account; what is/isn't preserved (honest, Section 2.2).
  2. Manual fallback: re-share media + text summary.
- Validation report stored on `ImportPackage` and shown in UI.

---

## 10. Frontend Design

- **Vite + React 18 + TS + Tailwind**, TanStack Query (server state + polling), react-router.
- Pages:
  - **Dashboard** — cards: accounts, exports, storage usage, running jobs.
  - **Accounts** — list, add-account wizard (phone → OTP → 2FA), status badges.
  - **Exports** — chat search → start export; per-export view with progress bar, counts,
    speed, ETA, pause/resume/cancel/retry; file browser (JSON/SQLite/HTML + media preview).
  - **Migration** — build WhatsApp packages from exports; test-package builder (count grid).
  - **Import Assistant** — 4-step wizard (select → validate → stats → instructions).
  - **Jobs** — live progress bars, ETA, logs, error panels.
- Progress via 2s polling of `/api/jobs` + per-export endpoints (SSE deferred; polling is
  simpler and robust behind nginx).

---

## 11. Security Model

- **No plaintext anywhere:** Telegram session strings Fernet-encrypted at rest;
  `api_hash` encrypted; dashboard passwords argon2-hashed; secrets only in env (`.env`).
- **Dashboard auth:** single admin (email/password) seeded from env on first boot;
  JWT bearer for all `/api` routes except `/auth/login`; CORS locked to the nginx origin;
  slowapi rate limiting on login.
- **Audit log:** middleware records every mutating call (who, what, resource, ip).
- **Files:** exports served only through authenticated API endpoints; nginx never serves
  the exports volume directly.
- **Network:** TLS termination at nginx (documented certbot path); app containers on an
  internal compose network; no exposed ports except 80/443.
- **Telegram-side hygiene:** users supply their own `api_id`/`api_hash` (my.telegram.org);
  docs include responsible-use and ban-risk guidance.

---

## 12. Testing Strategy

| Layer | What | Tooling |
|---|---|---|
| Unit | converter line formatting + sender-never-merge, validator, progress/ETA math, session crypto round-trip, filename sanitizer, WhatsApp line parser | pytest |
| Integration | API flows with a **mocked Telethon client** (port/interface injected): login flow, export pipeline end-to-end, checkpoint resume after simulated crash, media retry, package validation; Postgres via compose/testcontainers, fakeredis | pytest-asyncio, httpx |
| Live (manual, env-gated) | real Telegram smoke test: login + export of a small chat — runs only when `LIVE_TELEGRAM_*` env is present | pytest marker |
| Frontend | production build passes; API contract tests against OpenAPI schema | vite build, pytest |

Coverage target ≥ 80% on `backend/app/services/` and `backend/app/core/`.

---

## 13. Docker & Deployment

- `docker-compose.yml` services: `postgres:16-alpine`, `redis:7-alpine`, `backend`
  (uvicorn, healthcheck), `worker` (celery, depends_on healthy backend), `frontend`
  (multi-stage → nginx static), `nginx` (reverse proxy, TLS-ready).
- Named volumes: `pgdata`, `redisdata`, `exports`.
- `.env.example` documents every variable (`POSTGRES_*`, `REDIS_URL`, `JWT_SECRET`,
  `SESSION_ENCRYPTION_KEY`, `ADMIN_EMAIL/ADMIN_PASSWORD`, `EXPORT_*` pacing knobs, …).
- One-command deploy: `docker compose up -d --build`. Ubuntu 24.04 + Docker CE supported;
  deployment doc covers host setup, firewall, certbot.

---

## 14. Documentation Deliverables

- `README.md` — architecture diagram, install, Docker deploy, Telegram API setup
  (my.telegram.org), env vars, usage guide, **Known Telegram Limitations** section.
- `docs/architecture.md`, `docs/api.md`, `docs/deployment.md`, `docs/development.md`.
- `CHANGELOG.md` updated every phase.

---

## 15. Development Phases (each ends with: run tests → update README/CHANGELOG → commit → push)

> Phase 0 (this document) is the approval gate. Pushing requires a git remote — see
> Open Questions #1.

**Phase 1 — Architecture, DB, Docker, Git**
1. Repo scaffold: `.gitignore`, README skeleton, CHANGELOG, LICENSE (MIT), pyproject.toml
2. `docker-compose.yml` + `.env.example` + nginx conf
3. FastAPI app factory, pydantic-settings config, async SQLAlchemy engine, Alembic setup
4. All SQLAlchemy models (§7) + initial migration
5. `/health` + `/api/stats` stubs; smoke test that compose stack boots
6. `docs/architecture.md`, `docs/deployment.md` first drafts
7. Tests: model round-trip vs Postgres, health endpoint
**Gate:** `docker compose up -d --build` green; `pytest` green.

**Phase 2 — Telegram authentication**
1. `core/crypto.py` — Fernet session encryption + argon2 hashing
2. TelegramSession CRUD API + status checks (get_me, limitation flags)
3. Login flow: phone → OTP → 2FA (AuthFlowManager + Redis state)
4. Encrypted session persistence + AccountClientPool
5. Dashboard auth: admin seed, JWT login, auth dependency, audit middleware, rate limit
Tests: crypto round-trip, auth API, mocked login flow. **Gate:** login flow green w/ mock.

**Phase 3 — Export engine**
1. Chat search API (`/accounts/{id}/chats?q=`)
2. Export lifecycle API (create/pause/resume/cancel/retry-failed)
3. Engine core: paced iteration, checkpointing, flood-wait handling, progress metrics
4. JSON streamed writer + `messages.json` schema
5. HTML exporter (index + paged) and SQLite archive builder
6. Celery task wiring + Redis progress keys
Tests: engine with mocked client (fixtures), crash-resume, progress math. **Gate:** green.

**Phase 4 — Media downloader**
1. Download queue, concurrency cap, streaming SHA-256, filename sanitization
2. Retry with backoff, resume-skip (size+hash), failed-media re-queue
Tests: downloader vs local files, retry, hash verification. **Gate:** green.

**Phase 5 — Converter & migration tooling**
1. WhatsApp converter (`_chat.txt` + media + manifest), strict sender mapping
2. Test migration builder (10/50/100/500/1000) with real media fixtures
3. Package validator + ImportPackage rows
4. Import Assistant API + honest instructions generator
Tests: golden-file conversion, sender-never-merge, validator reject cases. **Gate:** green.

**Phase 6 — Dashboard**
1. Vite+TS+Tailwind scaffold, routing, typed API client
2. Dashboard cards, Accounts wizard, Exports page (progress/ETA/controls), Jobs page,
   Migration page, Import Assistant wizard
3. nginx wiring; production build served
Tests: build passes, contract tests vs OpenAPI. **Gate:** UI exercised end-to-end.

**Phase 7 — Testing & release**
1. Full unit + integration suite; coverage ≥80% core
2. Live Telegram smoke test (manual, env-gated)
3. Finalize docs (api.md, development.md, README known-limitations)
4. `v1.0.0` tag; final push
**Gate:** all tests green, docs complete, one-command deploy verified.

---

## 16. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Telegram account limitation/ban | conservative pacing defaults, per-account caps, docs |
| Official importer skips text-only messages | documented + surfaced in UI; manual fallback offered |
| Hour-long exports die mid-run | checkpoint resume, acks_late, retry |
| Storage growth (media) | storage card on dashboard, per-export sizes, purge endpoint |
| Session invalidation / re-login | status checks, clear error + re-login flow in UI |
| Postgres row volume on 1M+ message chats | batched inserts, unique indexes, JSON file as canonical artifact |

---

## 17. Open Questions (blocking items)

1. **Git remote for `git push`:** none is configured on this machine. Please provide a
   remote URL (GitHub/GitLab/Gitea/self-hosted) + credentials method (SSH key / PAT), or
   confirm "local commits only until further notice".
2. **Dashboard auth:** confirm single admin user (env-seeded) is acceptable.
3. **HTML export flavor:** paged static HTML (index + per-5000-message pages) — OK?
4. **CI:** add GitHub Actions (lint + test + build) once the remote exists?
5. **Live-test credentials:** you will supply `api_id`/`api_hash` (my.telegram.org) + a
   test phone when we reach Phase 7's live smoke test. Never committed.
6. **UI language:** English default — confirm.

---

## 18. Definition of Done

All 7 phases complete; every phase committed and pushed (remote permitting); full test
suite green; coverage ≥80% on core services; README + docs complete and truthful about
Telegram's limitations; `docker compose up -d --build` deploys the whole product; import
assistant produces verified, honest migration instructions.
