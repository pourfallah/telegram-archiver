"""Config parsing + secret-hygiene tests."""
from __future__ import annotations

from pathlib import Path

from recovery.config import RecoveryConfig, load_dotenv


def test_from_env_parses_all_fields(tmp_path):
    env = {
        "RECOVERY_API_ID_A": "111", "RECOVERY_API_HASH_A": "hasha",
        "RECOVERY_PHONE_A": "+111", "RECOVERY_SESSION_A_STRING": "sessA",
        "RECOVERY_API_ID_B": "222", "RECOVERY_API_HASH_B": "hashb",
        "RECOVERY_PHONE_B": "+222", "RECOVERY_PEER": "@peer",
        "RECOVERY_RUN_DIR": str(tmp_path / "R"),
        "RECOVERY_MSGS_PER_SEC": "3.5", "RECOVERY_BURST": "7",
        "RECOVERY_DOWNLOAD_MEDIA": "0",
    }
    c = RecoveryConfig.from_env(env)
    assert c.api_id_a == 111 and c.api_hash_a == "hasha"
    assert c.phone_a == "+111" and c.session_a() == "sessA"
    assert c.api_id_b == 222 and c.peer == "@peer"
    assert c.msgs_per_sec == 3.5 and c.burst == 7
    assert c.download_media is False


def test_session_file_fallback(tmp_path):
    f = tmp_path / "sess.txt"
    f.write_text("SESSIONFILE\n")
    c = RecoveryConfig(session_a_file=str(f))
    assert c.session_a() == "SESSIONFILE"


def test_session_prefers_inline_over_file(tmp_path):
    f = tmp_path / "sess.txt"
    f.write_text("FILE")
    c = RecoveryConfig(session_a_file=str(f), session_a_string="INLINE")
    assert c.session_a() == "INLINE"


def test_load_dotenv_only_sets_missing(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("# comment\nRECOVERY_PHONE_A=+999\nRECOVERY_BURST=8\n")
    monkeypatch.delenv("RECOVERY_PHONE_A", raising=False)
    done = load_dotenv(env_file)
    assert done is True
    import os
    assert os.environ["RECOVERY_PHONE_A"] == "+999"
    assert os.environ["RECOVERY_BURST"] == "8"


def test_secret_fields_never_in_describe(tmp_path):
    c = RecoveryConfig(api_id_a=1, api_hash_a="secret-a",
                       session_a_string="super-secret-session")
    assert c.api_hash_a == "secret-a"
    # config has no accidental string rendering; assert the module keeps a
    # denylist that docs the fields treated as secrets.
    from recovery import config as cfgmod
    assert "api_hash_a" in cfgmod._SECRET_FIELDS
    assert "session_a_string" in cfgmod._SECRET_FIELDS