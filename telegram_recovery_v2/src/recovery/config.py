"""Runtime configuration for Telegram Recovery v2.

Plain env-file reader + dataclass. No third-party settings dependency so the
subproject stays small and self-contained. Secrets (api_id/api_hash/session)
are loaded from the environment or an optional ``.env`` file, and are never
logged or printed by any module.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# Field names never surfaced in logs or CLI output.
_SECRET_FIELDS = {"api_id_a", "api_hash_a", "api_id_b", "api_hash_b",
                  "session_a_string", "session_b_string"}


def load_dotenv(path: str | os.PathLike | None = None) -> bool:
    """Load ``KEY=VALUE`` lines from an env file (like ``.env``). In-place only."""
    path = Path(path) if path else Path.cwd() / ".env"
    if not path.exists():
        return False
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ.setdefault(key, value)
    return True


@dataclass
class RecoveryConfig:
    """Read once at startup; immutable thereafter."""

    # --- source A ---
    api_id_a: int | None = None
    api_hash_a: str | None = None
    phone_a: str | None = None
    session_a_file: str | None = None
    session_a_string: str | None = None
    # --- target B ---
    api_id_b: int | None = None
    api_hash_b: str | None = None
    phone_b: str | None = None
    session_b_file: str | None = None
    session_b_string: str | None = None
    # --- peer / run ---
    peer: str | None = None
    run_dir: Path = field(default_factory=lambda: Path("./test_runs"))
    # --- pacing ---
    msgs_per_sec: float = 2.0
    burst: int = 5
    # --- media ---
    download_media: bool = True
    media_resume: bool = True

    # ------------------------------------------------------------------
    def session_a(self) -> str | None:
        return self._session(self.session_a_string, self.session_a_file)

    def session_b(self) -> str | None:
        return self._session(self.session_b_string, self.session_b_file)

    @staticmethod
    def _session(inline: str | None, path: str | None) -> str | None:
        if inline:
            return inline
        if path:
            p = Path(path)
            if p.exists():
                return p.read_text(encoding="utf-8").strip()
        return None

    # ------------------------------------------------------------------
    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "RecoveryConfig":
        e = os.environ if env is None else env
        return cls(
            api_id_a=_int(e.get("RECOVERY_API_ID_A")),
            api_hash_a=_none(e.get("RECOVERY_API_HASH_A")),
            phone_a=_none(e.get("RECOVERY_PHONE_A")),
            session_a_file=_none(e.get("RECOVERY_SESSION_A_FILE")),
            session_a_string=_none(e.get("RECOVERY_SESSION_A_STRING")),
            api_id_b=_int(e.get("RECOVERY_API_ID_B")),
            api_hash_b=_none(e.get("RECOVERY_API_HASH_B")),
            phone_b=_none(e.get("RECOVERY_PHONE_B")),
            session_b_file=_none(e.get("RECOVERY_SESSION_B_FILE")),
            session_b_string=_none(e.get("RECOVERY_SESSION_B_STRING")),
            peer=_none(e.get("RECOVERY_PEER")),
            run_dir=Path(e.get("RECOVERY_RUN_DIR", "./test_runs")),
            msgs_per_sec=float(e.get("RECOVERY_MSGS_PER_SEC", "2.0")),
            burst=int(e.get("RECOVERY_BURST", "5")),
            download_media=_int(e.get("RECOVERY_DOWNLOAD_MEDIA", "1")) == 1,
            media_resume=_int(e.get("RECOVERY_MEDIA_RESUME", "1")) == 1,
        )


def _none(v: str | None) -> str | None:
    return (v or "").strip() or None


def _int(v: str | None) -> int | None:
    v = _none(v)
    return int(v) if v else None