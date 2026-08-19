#!/usr/bin/env bash
# Database helper — runs alembic against the docker-compose Postgres from the host.
#
# Usage:  scripts/db.sh <alembic-args...>
# Example:
#   scripts/db.sh revision --autogenerate -m "add column"
#   scripts/db.sh upgrade head
set -euo pipefail
cd "$(dirname "$0")/../backend"

# Load deployment secrets from the root .env (postgres credentials).
if [ -f ../.env ]; then
    set -a
    # shellcheck disable=SC1091
    . ../.env
    set +a
fi

export DATABASE_URL="postgresql+asyncpg://${POSTGRES_USER:-archiver}:${POSTGRES_PASSWORD:-change-me}@localhost:5432/${POSTGRES_DB:-telegram_archiver}"

exec alembic "$@"