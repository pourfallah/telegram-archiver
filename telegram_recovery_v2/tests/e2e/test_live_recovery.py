"""LIVE end-to-end recovery test (requires real Telegram credentials).

Run ONLY when RECOVERY_* live env + sessions are present:
    pytest -m live tests/e2e/

Capabilities that a given pair of accounts cannot exercise are recorded as
``NOT_AVAILABLE``, never silently skipped (project rule #58).
"""
from __future__ import annotations

import json
import os

import pytest

from recovery.config import RecoveryConfig, load_dotenv
from recovery.engine import TelegramRecoveryEngine
from recovery.telegram_client import RecoveryClient, default_connect

pytestmark = pytest.mark.live

LIVE_REQUIRED = (
    "RECOVERY_API_ID_A", "RECOVERY_API_HASH_A",
    "RECOVERY_API_ID_B", "RECOVERY_API_HASH_B",
)


@pytest.fixture
def live_config():
    load_dotenv()
    cfg = RecoveryConfig.from_env()
    missing = [k for k in LIVE_REQUIRED if not getattr(cfg, k.lower(), None)]
    if missing or not (cfg.session_a() and cfg.session_b()):
        pytest.skip(f"live creds/sessions not configured: missing {missing or 'sessions'}")
    return cfg


def _engine(cfg):
    src = RecoveryClient(cfg.api_id_a, cfg.api_hash_a, cfg.phone_a, connect=default_connect)
    tgt = RecoveryClient(cfg.api_id_b, cfg.api_hash_b, cfg.phone_b, connect=default_connect)
    return TelegramRecoveryEngine(src, tgt, cfg)


@pytest.mark.asyncio
async def test_live_full_recovery(live_config):
    eng = _engine(live_config)
    try:
        await eng.connect()
        result = await eng.full_test(max_messages=None, react=True)
    finally:
        await eng.close()
    report = result["report"]
    steps = result["steps"]
    assert steps["export"]["messages"] > 0
    assert steps["verify_export"]["ok"] is True
    assert steps["source_still_has"] is True
    # The decisive assertion is the report itself — write and surface it.
    with open(os.path.join(".", "test_runs", "live_" + eng.run.run_id + "_report.json"),
              "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    assert report["summary"]  # non-empty fidelity summary