"""Generated import packages and their validation results."""
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ImportPackage(Base):
    __tablename__ = "import_packages"

    id: Mapped[int] = mapped_column(primary_key=True)
    migration_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("migration_jobs.id", ondelete="SET NULL")
    )
    name: Mapped[str] = mapped_column(String(255))
    package_path: Mapped[str] = mapped_column(String(1024))
    format: Mapped[str] = mapped_column(String(16), default="whatsapp")

    messages_count: Mapped[int] = mapped_column(Integer, default=0)
    media_count: Mapped[int] = mapped_column(Integer, default=0)
    users_detected: Mapped[dict | None] = mapped_column(JSON)
    date_min: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    date_max: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # pending | valid | invalid | warnings
    validation_status: Mapped[str] = mapped_column(String(16), default="pending")
    validation_report: Mapped[dict | None] = mapped_column(JSON)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    migration_job: Mapped["MigrationJob | None"] = relationship()  # noqa: F821
