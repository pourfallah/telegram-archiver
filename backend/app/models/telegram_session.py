"""Telegram account sessions (one row per logged-in Telegram account)."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class TelegramSession(Base):
    """A Telegram user account bound to this server.

    ``session_encrypted`` holds a Fernet-encrypted Telethon session string.
    ``api_hash_encrypted`` holds the Fernet-encrypted api_hash. Neither is
    ever stored in plaintext.
    """

    __tablename__ = "telegram_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="SET NULL"), index=True
    )
    phone: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    api_id: Mapped[int] = mapped_column(Integer)
    api_hash_encrypted: Mapped[str] = mapped_column(Text)
    session_encrypted: Mapped[str | None] = mapped_column(Text)

    status: Mapped[str] = mapped_column(
        String(32),
        default="new",
        # new | auth_pending_code | auth_pending_2fa | active | limited | banned | error
    )
    last_error: Mapped[str | None] = mapped_column(Text)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user_account: Mapped["UserAccount | None"] = relationship(  # noqa: F821
        back_populates="telegram_sessions"
    )
    exports: Mapped[list["ChatExport"]] = relationship(  # noqa: F821
        back_populates="telegram_session"
    )
