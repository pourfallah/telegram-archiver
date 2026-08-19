"""Migration + import schemas."""
from datetime import datetime

from pydantic import BaseModel, Field


class MigrationCreate(BaseModel):
    export_id: int
    format: str = Field(default="whatsapp", pattern="^(whatsapp)$")


class MigrationPublic(BaseModel):
    id: int
    chat_export_id: int
    format: str
    status: str
    messages_converted: int
    media_copied: int
    output_dir: str | None
    error: str | None
    created_at: datetime
    finished_at: datetime | None


class TestPackageCreate(BaseModel):
    count: int = Field(default=100, ge=10, le=1000)


class ImportPackagePublic(BaseModel):
    id: int
    migration_job_id: int | None
    name: str
    package_path: str
    format: str
    messages_count: int
    media_count: int
    users_detected: dict | None
    date_min: datetime | None
    date_max: datetime | None
    validation_status: str
    validation_report: dict | None
    created_at: datetime


class ValidationRequest(BaseModel):
    package_id: int


class ValidationResult(BaseModel):
    validation_status: str
    issues: list[str]
    stats: dict


class InstructionsResult(BaseModel):
    package_id: int
    instructions: list[dict]
    instructions_path: str | None = None
