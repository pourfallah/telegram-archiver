"""telegram_recovery_v2 — self-contained Telegram recovery engine.

ONE engine (TelegramRecoveryEngine) for tests, CLI and the future web app.
"""

__version__ = "2.0.0"

from .engine import TelegramRecoveryEngine, new_run_id  # noqa: F401
