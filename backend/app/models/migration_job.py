"""Conversion jobs: Telegram export -> WhatsApp-compatible import package."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class MigrationJob(Base):
    __tablename__ = "migration_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_export_id: Mapped[int] = mapped_column(
        ForeignKey("chat_exports.id", ondelete="CASCADE"), index=True
    )
    format: Mapped[str] = mapped_column(String(16), default="whatsapp")

    status: Mapped[str] = mapped_column(
        String(16), default="queued", index=True
        # queued | running | completed | failed
    )
    messages_converted: Mapped[int] = mapped_column(Integer, default=0)
    media_copied: Mapped[int] = mapped_column(Integer, default=0)
    output_dir: Mapped[str | None] = mapped_column(String(1024))
    error: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    chat_export: Mapped["ChatExport"] = relationship()  # noqa: F821
