"""Chat export jobs — also carry the crash-resume checkpoint state."""
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ChatExport(Base):
    __tablename__ = "chat_exports"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_session_id: Mapped[int] = mapped_column(
        ForeignKey("telegram_sessions.id", ondelete="CASCADE"), index=True
    )

    chat_id: Mapped[int] = mapped_column(BigInteger)
    chat_title: Mapped[str] = mapped_column(String(255))
    chat_type: Mapped[str] = mapped_column(String(16), default="private")
    # private | group | channel
    format: Mapped[str] = mapped_column(String(16), default="all")
    # json | html | sqlite | all

    status: Mapped[str] = mapped_column(
        String(16),
        default="queued",
        index=True,
        # queued | running | paused | cancelled | failed | completed
    )

    # Progress / checkpoint state
    messages_processed: Mapped[int] = mapped_column(BigInteger, default=0)
    total_messages_est: Mapped[int | None] = mapped_column(BigInteger)
    files_downloaded: Mapped[int] = mapped_column(BigInteger, default=0)
    files_total: Mapped[int] = mapped_column(BigInteger, default=0)
    speed_mps: Mapped[float] = mapped_column(Float, default=0.0)
    eta_seconds: Mapped[int | None] = mapped_column(Integer)
    checkpoint_offset_id: Mapped[int | None] = mapped_column(BigInteger)
    checkpoint_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    options: Mapped[dict] = mapped_column(JSON, default=dict)
    export_dir: Mapped[str | None] = mapped_column(String(1024))
    error: Mapped[str | None] = mapped_column(Text)

    # Export self-check (SOURCE vs CANONICAL ARCHIVE). Import is disabled
    # until verified is True and verification.status == "PASS".
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    verification: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    telegram_session: Mapped["TelegramSession"] = relationship(  # noqa: F821
        back_populates="exports"
    )
    messages: Mapped[list["Message"]] = relationship(  # noqa: F821
        back_populates="chat_export", cascade="all, delete-orphan"
    )
    media_files: Mapped[list["MediaFile"]] = relationship(  # noqa: F821
        back_populates="chat_export", cascade="all, delete-orphan"
    )
