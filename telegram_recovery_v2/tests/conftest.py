"""Shared fixtures for hermetic recovery-v2 tests."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for p in (_ROOT, Path(__file__).parent):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from fakes import FakeClient, FakeRecoveryClient  # noqa: E402

from recovery.config import RecoveryConfig  # noqa: E402
from recovery.engine import TelegramRecoveryEngine  # noqa: E402


@pytest.fixture
def run_dir(tmp_path):
    d = tmp_path / "runs"
    d.mkdir()
    return d


@pytest.fixture
def source_client():
    client = FakeClient()
    return client


@pytest.fixture
def config(run_dir):
    return RecoveryConfig(
        api_id_a=1, api_hash_a="a", api_id_b=2, api_hash_b="b",
        phone_a="+111", phone_b="+222", peer="@peer",
        run_dir=run_dir, download_media=False,
    )


@pytest.fixture
def engine(source_client, run_dir, config):
    sc = FakeRecoveryClient(FakeClient(), my_id=100)
    tc = FakeRecoveryClient(FakeClient(), my_id=200)
    eng = TelegramRecoveryEngine(sc, tc, config)
    # engine uses src.get_peer (returns InputPeerUser) as .peer
    return eng


@pytest.fixture
def make_engine(run_dir):
    """Factory: build an offline engine over fake clients."""
    def _build(src_history=None, tgt_history=None, download_media=False,
               config: RecoveryConfig | None = None):
        src = FakeRecoveryClient(FakeClient(history=src_history or []), my_id=100)
        tgt = FakeRecoveryClient(FakeClient(history=tgt_history or []), my_id=200)
        cfg = config or RecoveryConfig(
            api_id_a=1, api_hash_a="a", api_id_b=2, api_hash_b="b",
            run_dir=run_dir, download_media=download_media,
        )
        e = TelegramRecoveryEngine(src, tgt, cfg)
        e.peer = "peer"
        return e
    return _build