# Configuration for the recovery v2 engine.

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent.parent


@dataclass
class AccountConfig:
    """One Telegram account (phone, api creds, session string)."""

    label: str  # "A" or "B"
    phone: str
    api_id: int
    api_hash: str
    session: str  # Telethon StringSession value

    def redacted(self) -> dict:
        return {
            "label": self.label,
            "phone": self.phone[:6] + "***",
            "api_id": self.api_id,
            "session_len": len(self.session),
        }


@dataclass
class RecoveryConfig:
    account_a: AccountConfig
    account_b: AccountConfig
    runs_dir: Path = field(default_factory=lambda: ROOT / "test_runs")
    proxy: str | None = None


def _account_from_env(label: str, fallback_api: tuple[int, str] | None) -> AccountConfig | None:
    phone = os.getenv(f"RECOVERY_ACCOUNT_{label}_PHONE", "")
    if not phone:
        return None
    api_id = os.getenv(f"RECOVERY_ACCOUNT_{label}_API_ID")
    api_hash = os.getenv(f"RECOVERY_ACCOUNT_{label}_API_HASH")
    if not (api_id and api_hash) and fallback_api:
        api_id, api_hash = str(fallback_api[0]), fallback_api[1]
    if not (api_id and api_hash):
        raise ValueError(f"RECOVERY_ACCOUNT_{label}_API_ID/API_HASH missing and no fallback set")
    session = os.getenv(f"RECOVERY_ACCOUNT_{label}_SESSION", "")
    if not session and ROOT.parent.name != "__not_set__":
        # Support file-based session strings (git-ignored) next to the package.
        p = ROOT / "secrets" / f"account_{label.lower()}.session_string"
        if p.exists():
            session = p.read_text().strip()
    if not session:
        raise ValueError(f"RECOVERY_ACCOUNT_{label}_SESSION missing (string or secrets/ file)")
    return AccountConfig(
        label=label,
        phone=phone,
        api_id=int(api_id),
        api_hash=api_hash,
        session=session,
    )


def _account_from_bundle_or_env(label: str, fallback_api: tuple[int, str] | None) -> AccountConfig | None:
    """Load credentials from secrets/account_<label>.* JSON bundle (authoritative)
    or env vars. Secrets bundle wins because it carries the session string."""
    bundle_api = ROOT / "secrets" / f"account_{label.lower()}.api.json"
    bundle_sess = ROOT / "secrets" / f"account_{label.lower()}.session_string"
    if bundle_api.exists() and bundle_sess.exists():
        d = json.loads(bundle_api.read_text())
        return AccountConfig(
            label=label,
            phone=str(d.get("phone", "")),
            api_id=int(d["api_id"]),
            api_hash=str(d["api_hash"]),
            session=bundle_sess.read_text().strip(),
        )
    return _account_from_env(label, fallback_api)


def load_config(env_file: Path | None = None) -> RecoveryConfig:
    """Build a RecoveryConfig from secrets/ bundles or environment/.env."""
    load_dotenv(env_file or ROOT / ".env", override=False)

    def _api_from_json(label: str) -> tuple[int, str] | None:
        p = ROOT / "secrets" / f"account_{label.lower()}.api.json"
        if p.exists():
            d = json.loads(p.read_text())
            return int(d["api_id"]), str(d["api_hash"])
        return None

    a = _account_from_bundle_or_env("A", _api_from_json("A"))
    b = _account_from_bundle_or_env("B", _api_from_json("B"))
    if a is None or b is None:
        raise ValueError(
            "Both accounts A and B must be configured (env or secrets/ bundles)"
        )
    runs_dir = os.getenv("RECOVERY_RUNS_DIR")
    proxy = os.getenv("RECOVERY_PROXY") or None
    return RecoveryConfig(
        account_a=a,
        account_b=b,
        runs_dir=Path(runs_dir) if runs_dir else ROOT / "test_runs",
        proxy=proxy,
    )
