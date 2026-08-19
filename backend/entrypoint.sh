#!/bin/sh
set -e

# Apply database migrations before serving.
echo "[entrypoint] applying migrations (alembic upgrade head)"
alembic upgrade head

echo "[entrypoint] starting: $*"
exec "$@"
