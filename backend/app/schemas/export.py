"""Chat search + export schemas."""
from datetime import datetime

from pydantic import BaseModel, Field

ChatType = str  # private | group | channel


class ChatSearchResult(BaseModel):
    id: int
    title: str
    type: str
    username: str | None = None
    access_hash: int | None = None


class ExportCreate(BaseModel):
    chat_id: int
    format: str = Field(default="all", pattern="^(json|html|sqlite|all)$")
    include_media: bool = True


class ExportPublic(BaseModel):
    id: int
    account_id: int
    chat_id: int
    chat_title: str
    chat_type: str
    format: str
    status: str
    messages_processed: int
    total_messages_est: int | None
    files_downloaded: int
    files_total: int
    speed_mps: float
    eta_seconds: int | None
    export_dir: str | None
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


class ExportProgress(BaseModel):
    status: str
    percent: float | None = None
    messages_processed: int
    total_messages_est: int | None
    files_downloaded: int
    files_total: int
    speed_mps: float
    eta_seconds: int | None
    checkpoint_offset_id: int | None
    error: str | None


class ExportFileEntry(BaseModel):
    path: str
    name: str
    size: int
    is_dir: bool
