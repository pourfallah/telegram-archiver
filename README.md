# Telegram Archive & Migration Suite

Self-hosted, production-grade toolkit to **export Telegram conversations** (messages,
media, full metadata) and **prepare migration/import packages** — with a modern web
dashboard, background workers, and honest documentation of what Telegram's API can and
cannot do.

> **Status:** in development — Phase 6 (dashboard) complete; Phase 7 (testing + release) next.
> See `PROJECT_PLAN.md` for the full plan; see `CHANGELOG.md` for progress.

---

## Architecture

```
┌──────────────┐   HTTPS    ┌───────────────┐        ┌────────────┐
│   Browser    │ ─────────► │ nginx (TLS)   │ ─────► │  Frontend  │
│ React SPA    │            │ reverse proxy │        │ (Vite/TS)  │
└──────────────┘            └──────┬────────┘        └────────────┘
                                   │ /api
                          ┌────────▼────────┐   SQL    ┌────────────┐
                          │   FastAPI       │ ───────► │ PostgreSQL │
                          │  (uvicorn)      │          └────────────┘
                          └────────┬────────┘   Redis  ┌────────────┐
                                   ├──────────────────► │   Redis    │
                          ┌────────▼────────┐          └────────────┘
                          │  Celery worker  │
                          │ export/convert  │
                          └────────┬────────┘
                                   │ MTProto (Telethon), encrypted sessions
                          ┌────────▼────────┐
                          │ Telegram servers│
                          └─────────────────┘
          exports/ volume shared by API + worker (media, packages)
```

- **FastAPI** — REST API, JWT dashboard auth, audit logging.
- **Celery + Redis** — long-running jobs (export, media download, conversion, validation)
  with DB checkpointing so crashes resume, not restart.
- **PostgreSQL** — models, export ledger, audit trail.
- **Telethon** — MTProto client; sessions encrypted at rest (Fernet).
- **React + TypeScript + Tailwind** — dashboard UI served behind nginx.

Detailed design: [docs/architecture.md](docs/architecture.md).

---

## Feature Overview

| Module | Status |
|---|---|
| Telegram account login (phone / OTP / 2FA), encrypted sessions, multi-account | ✅ Phase 2 complete |
| Dashboard authentication (JWT), audit logging, rate limiting | ✅ Phase 2 complete |
| Chat export → JSON / HTML / SQLite, checkpointed, pause / resume / cancel | ✅ Phase 3 complete |
| Media download (hashing, retry, concurrency cap) | ✅ Phase 4 complete |
| WhatsApp-compatible migration packages (`_chat.txt` + media) | ✅ Phase 5 complete |
| Test migration builder (10/50/100/500/1000 messages) | ✅ Phase 5 complete |
| Import assistant wizard with validation + honest instructions | ✅ Phase 5 complete |
| Web dashboard (cards, jobs, progress, ETA) | ✅ Phase 6 complete |
| Unit + integration tests, CI | Phase 7 (final) |

---

## Known Telegram Limitations (read before using)

Telegram's platform imposes hard limits that this product documents rather than fakes:

1. **No arbitrary history import.** There is no public API to restore/backfill messages
   into a Telegram chat. Telegram's *only* official import path (Telegram Desktop →
   Settings → Advanced → *Import from…*) accepts **WhatsApp, Line and KakaoTalk** export
   packages only. This suite therefore produces **WhatsApp-format packages** so history
   can be moved into a fresh Telegram account through the official importer.
2. **Importer caveats:** the official importer may **skip text-only messages**; reactions,
   edits, reply chains and forward provenance are **not preserved**; at least one media
   file is required for a package to be accepted. The Import Assistant surfaces all of
   this before you start.
3. **Access boundaries:** only chats the logged-in account can see are exportable —
   private groups/channels the account left or was removed from are not; **secret chats
   are never exportable**; messages deleted server-side are gone.
4. **Rate limits & account safety:** aggressive automation can trigger `FloodWait` and
   even account limitation. Exports are paced (default ≈1 msg/s, configurable) with
   automatic flood-wait handling. Use on accounts you own.
5. **Phone-number search** for arbitrary users is unreliable; search by username, chat
   title or chat id is supported.

---

## Installation

### Requirements

- Linux (Ubuntu 24.04 supported), macOS or Windows with Docker
- Docker Engine ≥ 24 + Docker Compose v2

### 1. Get Telegram API credentials

1. Log in at https://my.telegram.org with the phone number you'll archive from.
2. Go to **API development tools** → create an application.
3. Copy your **api_id** and **api_hash**. (You will enter them in the dashboard when
   adding an account — never commit them to git.)

### 2. Configure

```bash
cp .env.example .env
# Edit .env:
#   - set strong POSTGRES_PASSWORD / ADMIN_PASSWORD
#   - generate JWT_SECRET:      python3 -c "import secrets; print(secrets.token_urlsafe(48))"
#   - generate SESSION_ENCRYPTION_KEY:
#       python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
#     ⚠️ losing this key makes stored Telegram sessions unrecoverable — back it up.
```

### 3. Deploy

```bash
docker compose up -d --build
```

Open http://localhost — the dashboard loads; migrations apply automatically on boot.

### Environment variables

All documented in [.env.example](.env.example) and [docs/deployment.md](docs/deployment.md).

---

## Usage Guide (as features land)

1. **Add a Telegram account** — phone → OTP code → optional 2FA password. Session is
   encrypted and stored server-side.
2. **Search a chat** — by username, title or chat id.
3. **Start an export** — choose format (JSON / HTML / SQLite / all), watch live progress
   (percent when known, counts, speed, ETA), pause / resume / cancel / retry failed media.
   Output layout:
   ```
   exports/<account>/<chat_name>/
     messages.json
     database.sqlite
     media/{photos,videos,documents,voice,audio,stickers,gifs}/
   ```
4. **Build a migration package** — convert a completed export into a WhatsApp-style
   package (`_chat.txt` + media) for Telegram's official importer, or generate a test
   package (10/50/100/500/1000 messages).
5. **Import Assistant** — validate a package and follow the generated instructions.

---

## Development

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest                      # unit + integration tests
alembic upgrade head        # apply migrations
uvicorn app.main:app --reload

cd frontend
npm install
npm run dev                 # proxies /api to localhost:8000
```

See [docs/development.md](docs/development.md) for details.

---

## Documentation

- [docs/architecture.md](docs/architecture.md) — components, data flows, design decisions
- [docs/api.md](docs/api.md) — REST API reference
- [docs/deployment.md](docs/deployment.md) — Ubuntu 24.04 host setup, TLS, backups
- [docs/development.md](docs/development.md) — local dev, testing, contributing
- [PROJECT_PLAN.md](PROJECT_PLAN.md) — the full project plan & architecture review

## License

MIT — see [LICENSE](LICENSE). Use responsibly and in accordance with Telegram's
Terms of Service.
