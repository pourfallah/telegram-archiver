"""Hermetic tests for the recovery_sample_test harness helpers."""
from __future__ import annotations

import os

from recovery.config import RecoveryConfig
from recovery_v2 import recovery_sample_test as H


def test_load_session_string_env_first(tmp_path, monkeypatch):
    monkeypatch.setenv("RECOVERY_SESSION_A_STRING", "FROM_ENV")
    assert H.load_session_string("+989394430100", "RECOVERY_SESSION_A_STRING") == "FROM_ENV"


def test_load_session_string_falls_back_to_login_file(monkeypatch, tmp_path):
    monkeypatch.delenv("RECOVERY_SESSION_A_STRING", raising=False)
    monkeypatch.setattr(H.L, "SESSIONS_DIR", tmp_path / "sessions")
    spath = H.L.SESSIONS_DIR / "account_p989394430100.session"
    spath.parent.mkdir(parents=True, exist_ok=True)
    spath.write_text("SAVED_SESSION\n", encoding="utf-8")
    assert H.load_session_string("+989394430100", "RECOVERY_SESSION_A_STRING") == "SAVED_SESSION"


def test_prepare_config_wires_sessions(monkeypatch, tmp_path, run_dir):
    monkeypatch.setenv("RECOVERY_SESSION_A_STRING", "SESSION_A")
    monkeypatch.setenv("RECOVERY_SESSION_B_STRING", "SESSION_B")
    args = H._parser().parse_args(["--count", "25"])
    cfg = H.prepare_config(args)
    assert cfg.session_a() == "SESSION_A"
    assert cfg.session_b() == "SESSION_B"


def test_parser_defaults_to_dry_run():
    args = H._parser().parse_args(["--count", "25"])
    assert args.execute is False
    args2 = H._parser().parse_args(["--execute"])
    assert args2.execute is True
    assert args2.count == 25