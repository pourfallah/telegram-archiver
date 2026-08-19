"""Chat export lifecycle API: create, list, detail, progress, pause/resume/cancel, files."""
import logging
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_session_manager
from app.core.security import get_current_user
from app.database import get_session
from app.models import ChatExport, TelegramSession, UserAccount
from app.schemas.export import ExportProgress, ExportPublic
from app.services.session_manager import SessionManager

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/exports",
    tags=["exports"],
    dependencies=[Depends(get_current_user)],
)

DbSession = Annotated[AsyncSession, Depends(get_session)]
Manager = Annotated[SessionManager, Depends(get_session_manager)]


def _owned_exports_query(user_id: int):
    return select(ChatExport).join(
        TelegramSession, ChatExport.telegram_session_id == TelegramSession.id
    ).where(TelegramSession.user_account_id == user_id)


async def _get_owned_export(export_id: int, db: AsyncSession, user: UserAccount) -> ChatExport:
    export = await db.scalar(
        _owned_exports_query(user.id).where(ChatExport.id == export_id).options(
            selectinload(ChatExport.telegram_session)
        )
    )
    if export is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Export not found")
    return export


def _to_public(row: ChatExport) -> ExportPublic:
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


def _progress(row: ChatExport) -> ExportProgress:
    total = row.total_messages_est
    percent = None
    if total:
        percent = round(row.messages_processed / total * 100, 1)
    return ExportProgress(
        status=row.status,
        percent=percent,
        messages_processed=row.messages_processed,
        total_messages_est=total,
        files_downloaded=row.files_downloaded,
        files_total=row.files_total,
        speed_mps=row.speed_mps,
        eta_seconds=row.eta_seconds,
        checkpoint_offset_id=row.checkpoint_offset_id,
        error=row.error,
    )


@router.get("", response_model=list[ExportPublic])
async def list_exports(db: DbSession, user: Annotated[UserAccount, Depends(get_current_user)]):
    rows = (
        await db.scalars(
            _owned_exports_query(user.id)
            .order_by(ChatExport.created_at.desc())
        )
    ).all()
    return [_to_public(r) for r in rows]


@router.get("/{export_id}", response_model=ExportPublic)
async def get_export(
    export_id: int,
    db: DbSession,
    user: Annotated[UserAccount, Depends(get_current_user)],
):
    return _to_public(await _get_owned_export(export_id, db, user))


@router.get("/{export_id}/progress", response_model=ExportProgress)
async def get_progress(
    export_id: int,
    db: DbSession,
    user: Annotated[UserAccount, Depends(get_current_user)],
):
    return _progress(await _get_owned_export(export_id, db, user))


async def _set_status(export_id: int, db: AsyncSession, user: UserAccount, status: str) -> ExportPublic:
    export = await _get_owned_export(export_id, db, user)
    allowed = {"paused", "cancelled", "queued"}
    if status not in allowed:
        raise HTTPException(status_code=400, detail=f"Invalid transition to {status!r}")
    if export.status in ("completed", "cancelled") and status != "queued":
        raise HTTPException(status_code=409, detail=f"Export is {export.status} and cannot be paused")
    export.status = status
    await db.commit()
    await db.refresh(export)
    return _to_public(export)


@router.post("/{export_id}/pause", response_model=ExportPublic)
async def pause_export(
    export_id: int,
    db: DbSession,
    user: Annotated[UserAccount, Depends(get_current_user)],
):
    # Poll: the engine notices at its next checkpoint (<= CHECKPOINT_EVERY messages).
    return await _set_status(export_id, db, user, "paused")


@router.post("/{export_id}/cancel", response_model=ExportPublic)
async def cancel_export(
    export_id: int,
    db: DbSession,
    user: Annotated[UserAccount, Depends(get_current_user)],
):
    # Cancel keeps the partial export on disk; DELETE /api/exports/{id} purges it.
    return await _set_status(export_id, db, user, "cancelled")


@router.post("/{export_id}/resume", response_model=ExportPublic)
async def resume_export(
    export_id: int,
    request: Request,
    db: DbSession,
    user: Annotated[UserAccount, Depends(get_current_user)],
):
    export = await _get_owned_export(export_id, db, user)
    if export.status == "running":
        raise HTTPException(status_code=409, detail="Export is already running")
    export.status = "queued"
    await db.commit()
    await request.app.state.task_runner.enqueue(export_id)
    await db.refresh(export)
    return _to_public(export)


@router.delete("/{export_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_export(
    export_id: int,
    db: DbSession,
    user: Annotated[UserAccount, Depends(get_current_user)],
):
    export = await _get_owned_export(export_id, db, user)
    if export.status == "running":
        # Ask the engine to stop, then purge once it does.
        export.status = "cancelled"
        await db.commit()
    # Remove on-disk artifacts.
    if export.export_dir:
        Path(export.export_dir).parent  # noqa: B018 - export_dir is a directory; parent exists
        import shutil

        root = Path(export.export_dir)
        if root.exists():
            shutil.rmtree(root, ignore_errors=True)
    await db.delete(export)
    await db.commit()
    return None


@router.get("/{export_id}/files", response_model=list)
async def list_export_files(
    export_id: int,
    db: DbSession,
    user: Annotated[UserAccount, Depends(get_current_user)],
    path: str = "",
):
    from app.schemas.export import ExportFileEntry

    export = await _get_owned_export(export_id, db, user)
    if not export.export_dir:
        return []
    base = Path(export.export_dir).resolve()
    target = (base / path).resolve()
    if base not in target.parents and target != base:
        raise HTTPException(status_code=400, detail="Path escapes export directory")
    if not target.exists():
        raise HTTPException(status_code=404, detail="Path not found")
    if target.is_file():
        return [ExportFileEntry(
            path=str(target.relative_to(base)), name=target.name, size=target.stat().st_size, is_dir=False
        ).model_dump()]

    entries = []
    for child in sorted(target.iterdir()):
        if child.is_dir():
            entries.append(ExportFileEntry(path=str(child.relative_to(base)) + "/", name=child.name, size=0, is_dir=True))
        else:
            entries.append(ExportFileEntry(path=str(child.relative_to(base)), name=child.name, size=child.stat().st_size, is_dir=False))
    return [e.model_dump() for e in entries]


@router.get("/{export_id}/download")
async def download_file(
    export_id: int,
    db: DbSession,
    user: Annotated[UserAccount, Depends(get_current_user)],
    path: str,
):

    export = await _get_owned_export(export_id, db, user)
    if not export.export_dir:
        raise HTTPException(status_code=404, detail="Export has no files yet")
    base = Path(export.export_dir).resolve()
    target = (base / path).resolve()
    if base not in target.parents and target != base:
        raise HTTPException(status_code=400, detail="Path escapes export directory")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(target, filename=target.name, media_type="application/octet-stream")
