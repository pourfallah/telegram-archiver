"""Application configuration (pydantic-settings).

All values can be overridden via environment variables or a `.env` file
in the working directory. See `.env.example` for the full list.
"""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Application -------------------------------------------------
    app_name: str = "Telegram Archive & Migration Suite"
    debug: bool = False
    # Local dev default; docker-compose overrides with the Postgres URL.
    database_url: str = "sqlite+aiosqlite:///./data/dev.db"
    redis_url: str = "redis://localhost:6379/0"
    exports_dir: Path = Path("/data/exports")
    allowed_origins: str = "http://localhost"

    # --- Dashboard authentication -------------------------------------
    admin_email: str = "admin@example.com"
    admin_password: str = "change-me"
    jwt_secret: str = "dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60

    # --- Telegram session encryption -----------------------------------
    # Fernet key (base64). Required from Phase 2 onward; raising at startup
    # would break Phase 1 boot, so validation lives in core/crypto.
    session_encryption_key: str = ""

    # --- Export engine pacing (conservative defaults) -------------------
    export_msgs_per_sec: float = 1.0
    export_burst: int = 5
    checkpoint_every: int = 250
    media_concurrency: int = 2
    max_concurrent_sessions: int = 5

    @property
    def origin_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def sqlite_file(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    return Settings()
