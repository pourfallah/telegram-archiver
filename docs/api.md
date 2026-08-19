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

## Planned surface (Phases 2–6)

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/auth/login` | dashboard login → JWT |
| GET/POST/DELETE | `/api/accounts` | Telegram accounts + login flow (phone/OTP/2FA) |
| POST | `/api/accounts/{id}/check` | account status check |
| GET | `/api/accounts/{id}/chats?q=` | dialog search |
| POST | `/api/accounts/{id}/exports` | start export |
| GET | `/api/exports`, `/api/exports/{id}` | export list/detail |
| POST | `/api/exports/{id}/pause\|resume\|cancel\|retry-failed` | lifecycle controls |
| GET | `/api/exports/{id}/progress` | %, counts, speed, ETA, logs |
| GET | `/api/exports/{id}/files?path=` | authenticated artifact download |
| POST | `/api/migrations` | build WhatsApp package from export |
| POST | `/api/migrations/test` | test package builder (10/50/100/500/1000) |
| POST | `/api/import/validate` | validate package |
| GET | `/api/import/{id}/instructions` | import instructions |
| GET | `/api/jobs` | live job progress |
| GET | `/api/audit` | audit trail |
