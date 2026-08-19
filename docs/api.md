# API Reference

Base path: `/api` (proxied through nginx; direct: `http://localhost:8000`).

Interactive docs are available at `/api/docs` (Swagger UI) and the OpenAPI schema at
`/api/openapi.json`.

> **Status:** Phase 1 exposes infrastructure endpoints only. The full surface —
> accounts, OTP/2FA login, chats, exports, migrations, import assistant, jobs, audit —
> is specified in `PROJECT_PLAN.md` §8 and lands in Phases 2–6. This document is updated
> as endpoints ship.

## Infrastructure

### `GET /health`

Liveness + database readiness. `200` when the database answers `SELECT 1`,
`503` otherwise.

```json
{ "status": "ok", "version": "0.1.0", "db": "up" }
```

### `GET /api/stats`

Dashboard card numbers.

```json
{
  "accounts": 0,
  "exports_total": 0,
  "exports_running": 0,
  "storage_bytes": 0
}
```

## Deployed

### `POST /api/auth/login`

Dashboard login. Rate-limited (10/min per IP, Redis-backed, fail-open).

```json
// request
{ "email": "admin@example.com", "password": "..." }
// response 200
{
  "access_token": "eyJ...",
  "token_type": "bearer",
  "expires_in": 3600,
  "user": { "id": 1, "email": "admin@example.com", "is_admin": true }
}
```

All routes below require `Authorization: Bearer <token>`.

### Telegram accounts

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/accounts` | list the authenticated user's Telegram accounts |
| POST | `/api/accounts` | create account + start login (sends OTP) — body `{phone, api_id, api_hash}` |
| GET | `/api/accounts/{id}` | account detail |
| POST | `/api/accounts/{id}/code` | submit OTP code — body `{code}`; 200 with `status: "auth_pending_2fa"` when a 2FA password is additionally required |
| POST | `/api/accounts/{id}/2fa` | submit 2FA password — body `{password}` |
| POST | `/api/accounts/{id}/check` | verify connectivity/status (`active` / `limited`) + current user info |
| DELETE | `/api/accounts/{id}` | remove the account and its stored session |

Login errors are machine-readable: `{"detail": {"error": "<code>", "message": "..."}}`
with codes `invalid_phone`, `invalid_api`, `invalid_code`, `wrong_2fa_password`,
`flood_wait` (429), `not_authenticated`, `flow_expired`, `already_logged_in`,
`duplicate_phone` (409).

### Chat search & exports

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/accounts/{id}/chats?q=` | dialog search by username / title / id |
| POST | `/api/accounts/{id}/exports` | start an export — body `{chat_id, format: json\|html\|sqlite\|all, include_media}` |
| GET | `/api/exports` / `/api/exports/{id}` | list / detail exports (scoped per user) |
| GET | `/api/exports/{id}/progress` | live progress: status, percent, counts, speed, ETA, checkpoint |
| POST | `/api/exports/{id}/pause` / `cancel` / `resume` | lifecycle controls (terminal states guarded with 409) |
| GET | `/api/exports/{id}/files?path=` | authenticated artifact listing (path-traversal safe) |
| GET | `/api/exports/{id}/download?path=` | download an artifact |
| DELETE | `/api/exports/{id}` | purge the export + on-disk files |

### Migration & import

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/migrations` | build a WhatsApp package from a completed export — body `{export_id}` |
| GET | `/api/migrations` | list the user's migration jobs |
| POST | `/api/migrations/test` | test package builder — body `{count: 10\|50\|100\|500\|1000}` |
| GET | `/api/import/packages` | list generated import packages |
| POST | `/api/import/validate` | validate a package — body `{package_id}` → status + stats |
| GET | `/api/import/{id}/instructions` | step-by-step import instructions for a package |

### Not yet shipped (planned)

- `POST /api/exports/{id}/retry-failed` — re-queue failed media downloads.
- `GET /api/jobs` — consolidated live job feed (poll the exports endpoint today).
- `GET /api/audit` — audit trail listing.
