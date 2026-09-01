#!/bin/bash
# Menu-driven Telegram account login (python -m recovery_v2.login_accounts).
# Just run it: ./scripts/login_telegram_accounts.sh
set -e
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
if [ -d .venv ]; then
  source .venv/bin/activate
fi
exec python -m recovery_v2.login_accounts