"""Import job schemas (real Telegram MTProto import)."""
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class ImportJobStatus(StrEnum):
    QUEUED = "queued"
    VALIDATING = "validating"
    PEER_CHECKING = "peer_checking"
    IMPORT_INITIALIZED = "import_initialized"
    MEDIA_UPLOADING = "media_uploading"
    STARTING_IMPORT = "starting_import"
    WAITING = "waiting"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PeerInfo(BaseModel):
    peer_id: int | None = None
    peer_type: str | None = None
    username: str | None = None
    title: str | None = None
    mutual_contact: bool | None = None
    message_count: int | None = None


class PeerValidationResult(BaseModel):
    allowed: bool
    confirm_text: str = ""
    error_code: str | None = None
    error_message: str | None = None
    peer: PeerInfo


class TestImportRequest(BaseModel):
    export_id: int
    target_peer_id: int | None = None
    contact_identifier: str
    count: int = Field(default=10, ge=1, le=1000)


class ImportJobCreate(BaseModel):
    source_export_id: int
    target_account_id: int
    target_peer_id: int | None = None
    message_limit: int
    options: dict = {}


class ImportJobPublic(BaseModel):
    id: int
    source_export_id: int
    target_account_id: int
    target_peer_id: int | None = None
    message_limit: int
    status: ImportJobStatus
    options: dict
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    progress: dict | None = None


class TargetChat(BaseModel):
    id: int
    title: str | None = None
    username: str | None = None
    type: str
    peer_id: int
    access_hash: int | None = None
    message_count: int | None = None
    is_marked_unread: bool = False


class TargetChatsResponse(BaseModel):
    chats: list[TargetChat]


class StartImportRequest(BaseModel):
    export_id: int
    target_account_id: int
    target_peer_id: int
    message_limit: int = Field(default=10, ge=1, le=1000)
    contact_identifier: str = ""
