#!/usr/bin/env bash
# Runs the backend test suite against a real PostgreSQL database.
# Uses the telegram_archiver_test database (created by scripts/db-test-create.sh).
#
# Usage:  scripts/test-pg.sh
set -euo pipefail
cd "$(dirname "$0")/../backend"

if [ -f ../.env ]; then
    set -a
    # shellcheck disable=SC1091
    . ../.env
    set +a
fi

export TEST_DATABASE_URL="postgresql+asyncpg://${POSTGRES_USER:-archiver}:${POSTGRES_PASSWORD:-change-me}@localhost:5432/telegram_archiver_test"

exec pytest "$@"