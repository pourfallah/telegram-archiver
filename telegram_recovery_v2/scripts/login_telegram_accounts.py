#!/usr/bin/env python3
"""Thin wrapper so the login tool also runs directly from scripts/.

Requires: python scripts/login_telegram_accounts.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from recovery_v2 import login_accounts  # noqa: E402

if __name__ == "__main__":
    sys.exit(login_accounts.run())