# Deployment

Target: **Ubuntu 24.04 LTS** with Docker Engine + Compose v2. The whole product runs as
a single compose stack; nothing else needs installing on the host.

## 1. Host setup (Ubuntu 24.04)

```bash
sudo apt update && sudo apt upgrade -y
# Install Docker per docs.docker.com (docker.io from apt is acceptable for most uses):
sudo apt install -y docker.io docker-compose-v2
sudo systemctl enable --now docker
sudo usermod -aG docker $USER   # re-login afterwards
```

Firewall: allow 22 (SSH), 80, 443 only:

```bash
sudo ufw allow OpenSSH && sudo ufw allow 80/tcp && sudo ufw allow 443/tcp && sudo ufw enable
```

## 2. Get the code & configure

```bash
git clone <your-remote> telegram-archiver
cd telegram-archiver
cp .env.example .env
$EDITOR .env
```

Required secret generation:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"          # JWT_SECRET
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# SESSION_ENCRYPTION_KEY — BACK THIS UP. Losing it makes stored Telegram sessions unusable.
```

## 3. Deploy

```bash
docker compose up -d --build
docker compose ps            # all services healthy
```

The backend entrypoint runs `alembic upgrade head` automatically on every boot.
Dashboard: http://localhost (default admin login is seeded in Phase 2 from
`ADMIN_EMAIL` / `ADMIN_PASSWORD`).

Upgrades:

```bash
git pull
docker compose up -d --build
```

## 4. TLS (optional but recommended)

1. Point a DNS A record at the server.
2. Put the domain into `nginx/default.conf` (uncomment the 443 server block).
3. Obtain certificates:

```bash
sudo apt install -y certbot
sudo certbot certonly --standalone -d your.domain.example
# then restart nginx:
docker compose restart nginx
```

> The `server_name` inside `nginx/default.conf` and the cert paths in the 443 block must
> match your domain.

## 5. Backups

- **PostgreSQL:** `docker compose exec postgres pg_dump -U archiver telegram_archiver > backup.sql`
  (run on a schedule; e.g. cron).
- **exports/ volume:** back up the volume contents (or bind-mount `exports/` to a host
  directory in `docker-compose.yml` for simpler rsync backups).
- **`.env`:** back this up — it contains `SESSION_ENCRYPTION_KEY` and `JWT_SECRET`.

## 6. Operational notes

- Exports are long-running; the Celery worker is the process doing the work. Check
  `docker compose logs -f worker` for task logs.
- Storage: media can consume a lot of disk; the dashboard shows storage usage and
  exports can be purged via the API.
- Resource tuning: `MAX_CONCURRENT_SESSIONS`, `EXPORT_MSGS_PER_SEC` and
  `MEDIA_CONCURRENCY` in `.env` control how aggressively Telegram is polled. Start
  conservative (defaults) to protect accounts from limitation.

## 7. Environment variables

| Variable | Purpose |
|---|---|
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | database credentials |
| `REDIS_URL` | Redis connection for broker + progress |
| `DATABASE_URL` | backend DB URL (compose builds it from the above) |
| `EXPORTS_DIR` | exports volume mount point inside containers |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | seeded dashboard admin (Phase 2) |
| `JWT_SECRET` | signs dashboard JWTs |
| `SESSION_ENCRYPTION_KEY` | Fernet key encrypting Telegram sessions at rest |
| `EXPORT_MSGS_PER_SEC` / `EXPORT_BURST` | message fetch pacing (flood-wait avoidance) |
| `CHECKPOINT_EVERY` | crash-resume granularity (messages) |
| `MEDIA_CONCURRENCY` | parallel media downloads per export |
| `MAX_CONCURRENT_SESSIONS` | live Telegram sessions across all accounts |
| `ALLOWED_ORIGINS` | comma-separated CORS origins |
| `DEBUG` | FastAPI debug mode |
