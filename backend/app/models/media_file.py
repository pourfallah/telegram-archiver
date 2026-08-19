"""Per-file media download state."""
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class MediaFile(Base):
    __tablename__ = "media_files"
    __table_args__ = (
        UniqueConstraint(
            "chat_export_id", "message_id", "media_type", name="uq_export_media"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_export_id: Mapped[int] = mapped_column(
        ForeignKey("chat_exports.id", ondelete="CASCADE"), index=True
    )
    message_id: Mapped[int] = mapped_column(BigInteger)

    # photo | video | document | voice | audio | sticker | gif | animation
    media_type: Mapped[str] = mapped_column(String(16))
    mime_type: Mapped[str | None] = mapped_column(String(128))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    original_filename: Mapped[str | None] = mapped_column(String(512))
    file_path: Mapped[str | None] = mapped_column(String(1024))
    sha256: Mapped[str | None] = mapped_column(String(64))

    # pending | downloading | downloaded | failed
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    extra: Mapped[dict | None] = mapped_column(JSON)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    chat_export: Mapped["ChatExport"] = relationship(  # noqa: F821
        back_populates="media_files"
    )
