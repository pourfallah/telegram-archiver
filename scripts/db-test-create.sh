#!/usr/bin/env bash
# Creates (or resets) the dedicated Postgres test database.
#
# Usage:  scripts/db-test-create.sh
set -euo pipefail
cd "$(dirname "$0")/.."

docker compose exec -T postgres psql -U "${POSTGRES_USER:-archiver}" -d "${POSTGRES_DB:-telegram_archiver}" \
  -c "DROP DATABASE IF EXISTS telegram_archiver_test;" \
  -c "CREATE DATABASE telegram_archiver_test;"