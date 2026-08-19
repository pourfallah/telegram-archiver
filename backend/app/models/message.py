"""Exported message ledger rows.

The streamed ``messages.json`` file remains the canonical export artifact;
this table is the queryable index used for stats, validation and search.
"""
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
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("chat_export_id", "message_id", name="uq_export_message"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_export_id: Mapped[int] = mapped_column(
        ForeignKey("chat_exports.id", ondelete="CASCADE"), index=True
    )

    message_id: Mapped[int] = mapped_column(BigInteger)
    # original Telegram message id stays in message_id; grouped_id preserves
    # album/media-group membership so collages can be rebuilt on import.
    grouped_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    date: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    edit_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    sender_id: Mapped[int | None] = mapped_column(BigInteger)
    sender_name: Mapped[str | None] = mapped_column(String(255))
    sender_username: Mapped[str | None] = mapped_column(String(255))

    text: Mapped[str | None] = mapped_column(Text)
    entities: Mapped[dict | None] = mapped_column(JSON)
    reply_to_message_id: Mapped[int | None] = mapped_column(BigInteger)
    forwarded_from: Mapped[dict | None] = mapped_column(JSON)
    reactions: Mapped[dict | None] = mapped_column(JSON)
    views: Mapped[int | None] = mapped_column(Integer)

    media_count: Mapped[int] = mapped_column(Integer, default=0)
    media_types: Mapped[dict | None] = mapped_column(JSON)

    chat_export: Mapped["ChatExport"] = relationship(  # noqa: F821
        back_populates="messages"
    )
