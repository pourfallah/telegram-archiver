"""Shared helpers for the recovery-v2 run scripts."""
from __future__ import annotations

import sys

from recovery.config import RecoveryConfig, load_dotenv
from recovery.engine import TelegramRecoveryEngine
from recovery.telegram_client import RecoveryClient, default_connect


def get_config() -> RecoveryConfig:
    load_dotenv()
    return RecoveryConfig.from_env()


def build_engine(config: RecoveryConfig | None = None) -> TelegramRecoveryEngine:
    cfg = config or get_config()
    if not (cfg.api_id_a and cfg.api_hash_a and cfg.session_a()):
        sys.exit("SOURCE A not configured (RECOVERY_API_ID_A/HASH_A/SESSION_A_*)")
    if not (cfg.api_id_b and cfg.api_hash_b and cfg.session_b()):
        sys.exit("TARGET B not configured (RECOVERY_API_ID_B/HASH_B/SESSION_B_*)")
    src = RecoveryClient(cfg.api_id_a, cfg.api_hash_a, cfg.phone_a, connect=default_connect)
    tgt = RecoveryClient(cfg.api_id_b, cfg.api_hash_b, cfg.phone_b, connect=default_connect)
    eng = TelegramRecoveryEngine(src, tgt, cfg)
    return eng