"""Import job records (real Telegram MTProto import lifecycle)."""
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, func, BigInteger
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ImportJob(Base):
    __tablename__ = "import_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_export_id: Mapped[int] = mapped_column(
        ForeignKey("chat_exports.id", ondelete="CASCADE")
    )
    target_account_id: Mapped[int] = mapped_column(
        ForeignKey("telegram_sessions.id", ondelete="CASCADE")
    )
    target_peer_id: Mapped[int | None] = mapped_column(Integer)

    # max messages to import (for test mode); None = full
    message_limit: Mapped[int | None] = mapped_column(Integer)

    # QUEUED | VALIDATING | PEER_CHECKING | IMPORT_INITIALIZED | MEDIA_UPLOADING
    # | STARTING_IMPORT | WAITING | VERIFYING | COMPLETED | PARTIAL | FAILED | CANCELLED
    status: Mapped[str] = mapped_column(String(24), default="queued")

    # flexible options: contact_identifier, test_mode, etc.
    options: Mapped[dict] = mapped_column(JSON, default=dict)

    # progress snapshot
    progress: Mapped[dict | None] = mapped_column(JSON)

    # import_id returned by initHistoryImport
    import_id: Mapped[int | None] = mapped_column(BigInteger)

    error: Mapped[str | None] = mapped_column(String(1024))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    source_export: Mapped["ChatExport"] = relationship()  # noqa: F821
    target_account: Mapped["TelegramSession"] = relationship()  # noqa: F821
