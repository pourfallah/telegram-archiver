"""Chat search + export creation (account-scoped)."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session_manager
from app.core.security import get_current_user
from app.database import get_session
from app.models import ChatExport, TelegramSession, UserAccount
from app.schemas.export import ChatSearchResult, ExportCreate, ExportPublic
from app.services.canonical_archive import build_canonical_archive as _build_canonical_archive
from app.services.session_manager import SessionManager
from app.services.telegram_utils import serialize_input_peer

router = APIRouter(
    prefix="/api/accounts",
    tags=["chats"],
    dependencies=[Depends(get_current_user)],
)

DbSession = Annotated[AsyncSession, Depends(get_session)]
Manager = Annotated[SessionManager, Depends(get_session_manager)]


async def _get_owned_account(account_id: int, db: AsyncSession, user: UserAccount) -> TelegramSession:
    row = await db.scalar(
        select(TelegramSession).where(
            TelegramSession.id == account_id,
            TelegramSession.user_account_id == user.id,
        )
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
    return row


def _chat_result(entity) -> ChatSearchResult:
    eid = getattr(entity, "id", 0)
    title = getattr(entity, "title", None) or getattr(entity, "first_name", None) or str(eid)
    if getattr(entity, "last_name", None):
        title = f"{title} {entity.last_name}"
    ctype = _chat_type(entity)
    return ChatSearchResult(
        id=eid,
        title=title,
        type=ctype,
        username=getattr(entity, "username", None),
        access_hash=getattr(entity, "access_hash", None),
    )


def _chat_type(entity) -> str:
    """Map a Telethon entity to private | group | channel."""
    kind = getattr(entity, "_kind", None)
    if kind in ("group", "channel", "private"):
        return kind
    name = type(entity).__name__
    if name.startswith("User") or hasattr(entity, "first_name"):
        return "private"
    if name.endswith("Channel") or getattr(entity, "broadcast", False):
        return "channel"
    if name.endswith("Chat"):
        return "group"
    if getattr(entity, "title", None) and getattr(entity, "id", 0) < 0:
        return "channel" if getattr(entity, "megagroup", False) else "group"
    return "private"


async def _to_export_public(row: ChatExport) -> ExportPublic:
    from app.schemas.export import ExportPublic

    return ExportPublic(
        id=row.id,
        account_id=row.telegram_session_id,
        chat_id=row.chat_id,
        chat_title=row.chat_title,
        chat_type=row.chat_type,
        format=row.format,
        status=row.status,
        messages_processed=row.messages_processed,
        total_messages_est=row.total_messages_est,
        files_downloaded=row.files_downloaded,
        files_total=row.files_total,
        speed_mps=row.speed_mps,
        eta_seconds=row.eta_seconds,
        export_dir=row.export_dir,
        error=row.error,
        started_at=row.started_at,
        finished_at=row.finished_at,
        created_at=row.created_at,
    )


@router.get("/{account_id}/chats", response_model=list[ChatSearchResult])
async def search_chats(
    account_id: int,
    request: Request,
    db: DbSession,
    user: Annotated[UserAccount, Depends(get_current_user)],
    manager: Manager,
    q: str = "",
):
    account = await _get_owned_account(account_id, db, user)
    if account.status != "active":
        raise HTTPException(status_code=400, detail={"error": "not_active", "message": "Account is not logged in"})

    client, release = await manager.acquire_client(account)
    try:
        results: dict[int, ChatSearchResult] = {}

        # 1) Exact resolution: username / phone / id.
        query = q.strip()
        if query:
            try:
                entity = await client.get_entity(query)
                result = _chat_result(entity)
                result.title += " (exact)"  # mark exact matches for the UI
                results[result.id] = result
            except Exception:
                pass  # not resolvable as exact — rely on dialog filtering

        # 2) Dialog scan with substring filter on title/username.
        dialogs = await client.get_dialogs(limit=100)
        for dialog in dialogs:
            entity = dialog.entity
            title = (getattr(entity, "title", None)
                     or getattr(entity, "first_name", None) or "").lower()
            username = (getattr(entity, "username", None) or "").lower()
            if not query or query.lower() in title or query.lower() in username:
                result = _chat_result(entity)
                results.setdefault(result.id, result)
        return list(results.values())[:50]
    finally:
        await release()


@router.post("/{account_id}/exports", response_model=ExportPublic, status_code=status.HTTP_201_CREATED)
async def create_export(
    account_id: int,
    payload: ExportCreate,
    request: Request,
    db: DbSession,
    user: Annotated[UserAccount, Depends(get_current_user)],
    manager: Manager,
):
    account = await _get_owned_account(account_id, db, user)
    if account.status != "active":
        raise HTTPException(status_code=400, detail={"error": "not_active", "message": "Account is not logged in"})

    client, release = await manager.acquire_client(account)
    try:
        entity = await client.get_entity(payload.chat_id)
    except Exception:
        try:
            entity = await client.get_entity(str(payload.chat_id))
        except Exception as exc2:
            raise HTTPException(
                status_code=404,
                detail={"error": "chat_not_found", "message": "Could not resolve the chat"},
            ) from exc2

    result = _chat_result(entity)
    input_peer = serialize_input_peer(await client.get_input_entity(entity))
    await release()

    export = ChatExport(
        telegram_session_id=account.id,
        chat_id=result.id,
        chat_title=result.title,
        chat_type=result.type,
        format=payload.format,
        status="queued",
        options={"input_peer": input_peer, "include_media": payload.include_media},
    )
    db.add(export)
    await db.commit()
    await db.refresh(export)

    request.app.state.task_runner.enqueue(export.id)
    return await _to_export_public(export)


@router.post("/{account_id}/exports/{export_id}/archive")
async def build_archive(
    account_id: int,
    export_id: int,
    db: DbSession,
    user: Annotated[UserAccount, Depends(get_current_user)],
):
    """Build the loss-minimizing canonical archive from an export."""
    from pathlib import Path

    account = await _get_owned_account(account_id, db, user)
    export = await db.scalar(
        select(ChatExport).where(
            ChatExport.id == export_id, ChatExport.telegram_session_id == account.id
        )
    )
    if export is None:
        raise HTTPException(status_code=404, detail="Export not found")
    if not export.export_dir:
        raise HTTPException(status_code=400, detail="Export has no data yet")
    out_dir = Path(export.export_dir) / "archive"
    stats = _build_canonical_archive(
        Path(export.export_dir),
        out_dir,
        {"id": export.chat_id, "title": export.chat_title, "type": export.chat_type},
    )
    return {"export_id": export.id, "archive_dir": str(out_dir), **stats}
