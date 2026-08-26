"""Peer validation and Telegram history-import API endpoints."""
import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session_manager
from app.core.security import get_current_user
from app.database import get_session
from app.models import ChatExport, ImportJob, TelegramSession, UserAccount
from app.schemas.import_job import (
    ImportJobPublic,
    ImportJobStatus,
    PeerInfo,
    PeerValidationResult,
    StartImportRequest,
    TargetChat,
    TargetChatsResponse,
    TestImportRequest,
)
from app.services.canonical_archive import build_canonical_archive
from app.services.session_manager import SessionManager
from app.services.telegram_import import (
    ImportProtocolError,
    TelegramImporter,
)

router = APIRouter(
    prefix="/api/import",
    tags=["import"],
    dependencies=[Depends(get_current_user)],
)

DbSession = Annotated[AsyncSession, Depends(get_session)]
Manager = Annotated[SessionManager, Depends(get_session_manager)]


async def _get_owned_account(account_id: int, db: DbSession, user) -> TelegramSession:
    row = await db.scalar(
        select(TelegramSession).where(
            TelegramSession.id == account_id,
            TelegramSession.user_account_id == user.id,
        )
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
    return row


async def _get_owned_export(export_id: int, db: DbSession, user) -> ChatExport:
    row = await db.scalar(
        select(ChatExport).join(TelegramSession).where(
            ChatExport.id == export_id,
            TelegramSession.user_account_id == user.id,
        )
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Export not found")
    return row


def _http_import_error(exc: ImportProtocolError) -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={"error": exc.error_code, "message": exc.message},
    )


def _to_peer_info(info: dict) -> PeerInfo:
    return PeerInfo(
        peer_id=info.get("peer_id"),
        peer_type=info.get("peer_type"),
        username=info.get("username"),
        title=info.get("title"),
        mutual_contact=info.get("mutual_contact"),
        message_count=info.get("current_message_count"),
    )


@router.post("/{account_id}/validate-peer", response_model=PeerValidationResult)
async def validate_peer(
    account_id: int,
    payload: TestImportRequest,  # reusing for peer identifier
    db: DbSession,
    user: Annotated[UserAccount, Depends(get_current_user)],
    manager: Manager,
):
    """
    Validate whether a target peer can receive history import.

    Steps:
    1. Resolve the peer from the source account's session (Account A).
    2. Call messages.checkHistoryImportPeer(peer).
    3. Return the real Telegram confirm text + eligibility.
    """
    account = await _get_owned_account(account_id, db, user)
    if account.status != "active":
        raise HTTPException(status_code=400, detail="Account is not logged in")

    client, _release = await manager.acquire_client(account)
    importer = TelegramImporter(client)

    # Resolve peer — payload should contain contact identifier (username/phone/id)
    identifier = payload.contact_identifier
    try:
        peer, ent = await importer.resolve_peer(identifier)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not resolve peer: {exc}") from exc

    # Gather pre-flight info
    info = await importer.peer_info(peer, ent)

    # Actual Telegram peer check
    try:
        check = await importer.check_history_import_peer(peer)
    except ImportProtocolError as exc:
        return PeerValidationResult(
            allowed=False,
            confirm_text="",
            error_code=exc.error_code,
            error_message=exc.message,
            peer=_to_peer_info(info),
        )

    return PeerValidationResult(
        allowed=True,
        confirm_text=check.get("confirm_text", ""),
        peer=_to_peer_info(info),
    )


@router.post("/{account_id}/test-import", response_model=ImportJobPublic, status_code=status.HTTP_201_CREATED)
async def start_test_import(
    account_id: int,
    payload: TestImportRequest,
    db: DbSession,
    user: Annotated[UserAccount, Depends(get_current_user)],
    manager: Manager,
):
    """
    Start a test import job from a real export.

    This is the end-to-end test path:
    1. Select source export (Account A's export)
    2. Select target peer (Account B's existing A<->B chat)
    3. Choose message count (10/50/100/...)
    4. Run full import protocol and verification.
    """
    account = await _get_owned_account(account_id, db, user)
    if account.status != "active":
        raise HTTPException(status_code=400, detail="Account is not logged in")

    export = await _get_owned_export(payload.export_id, db, user)

    # HARD GATE: the source archive must be verified against the live source
    # before ANY import. Never import from an unverified/lossy archive.
    if not export.verified or (export.verification or {}).get("status") != "PASS":
        raise HTTPException(
            status_code=409,
            detail={
                "error": "export_not_verified",
                "message": (
                    "Source archive is incomplete or inconsistent. Import is "
                    "disabled until the source archive is verified."
                ),
            },
        )

    # Build canonical archive if not already present
    from pathlib import Path
    export_dir = Path(export.export_dir)
    archive_dir = export_dir / "archive"
    if not archive_dir.exists():
        _ = build_canonical_archive(
            export_dir, archive_dir,
            {"id": export.chat_id, "title": export.chat_title, "type": export.chat_type},
        )
    else:
        import json
        _ = json.loads((archive_dir / "manifest.json").read_text())

    # Create import job record
    job = ImportJob(
        source_export_id=export.id,
        target_account_id=account_id,
        target_peer_id=payload.target_peer_id,
        message_limit=payload.count,
        status=ImportJobStatus.QUEUED,
        options={
            "contact_identifier": payload.contact_identifier,
            "test_mode": True,
        },
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    # Dispatch the Celery task so the worker actually runs the import
    from app.workers.import_tasks import run_import
    run_import.delay(job.id)

    return ImportJobPublic(
        id=job.id,
        source_export_id=job.source_export_id,
        target_account_id=job.target_account_id,
        target_peer_id=job.target_peer_id,
        message_limit=job.message_limit,
        status=job.status,
        options=job.options,
        created_at=job.created_at,
    )


@router.get("/jobs", response_model=list[ImportJobPublic])
async def list_import_jobs(
    db: DbSession,
    user: Annotated[UserAccount, Depends(get_current_user)],
):
    rows = (
        await db.scalars(
            select(ImportJob).join(ChatExport).join(TelegramSession).where(
                TelegramSession.user_account_id == user.id
            ).order_by(ImportJob.created_at.desc())
        )
    ).all()
    return [
        ImportJobPublic(
            id=r.id,
            source_export_id=r.source_export_id,
            target_account_id=r.target_account_id,
            target_peer_id=r.target_peer_id,
            message_limit=r.message_limit,
            status=r.status,
            options=r.options,
            created_at=r.created_at,
        )
        for r in rows
    ]


@router.get("/jobs/{job_id}", response_model=ImportJobPublic)
async def get_import_job(
    job_id: int,
    db: DbSession,
    user: Annotated[UserAccount, Depends(get_current_user)],
):
    row = await db.scalar(
        select(ImportJob).join(ChatExport).join(TelegramSession).where(
            ImportJob.id == job_id,
            TelegramSession.user_account_id == user.id,
        )
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Import job not found")
    return ImportJobPublic(
        id=row.id,
        source_export_id=row.source_export_id,
        target_account_id=row.target_account_id,
        target_peer_id=row.target_peer_id,
        message_limit=row.message_limit,
        status=row.status,
        options=row.options,
        created_at=row.created_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
        error=row.error,
        progress=row.progress,
    )


@router.post("/jobs/{job_id}/start")
async def start_import_job(
    job_id: int,
    db: DbSession,
    user: Annotated[UserAccount, Depends(get_current_user)],
):
    """Dispatch the Celery task to run the import job."""
    from app.workers.import_tasks import run_import

    row = await db.scalar(
        select(ImportJob).join(ChatExport).join(TelegramSession).where(
            ImportJob.id == job_id,
            TelegramSession.user_account_id == user.id,
        )
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Import job not found")

    if row.status not in ("queued", "failed", "cancelled"):
        raise HTTPException(status_code=400, detail=f"Job cannot be started from status {row.status}")

    run_import.delay(job_id)
    return {"job_id": job_id, "dispatched": True}


@router.post("/start-real", response_model=ImportJobPublic, status_code=status.HTTP_201_CREATED)
async def start_real_import(
    payload: StartImportRequest,
    db: DbSession,
    user: Annotated[UserAccount, Depends(get_current_user)],
    manager: Manager,
):
    """
    Start a REAL Telegram MTProto history import.

    This is the full production import path (not test).
    Requires explicit user confirmation via the pre-flight UI step.
    """
    target_account = await _get_owned_account(payload.target_account_id, db, user)
    if target_account.status != "active":
        raise HTTPException(status_code=400, detail="Target account is not logged in")

    export = await _get_owned_export(payload.export_id, db, user)

    # HARD GATE: the source archive must be verified against the live source
    # before ANY import. Never import from an unverified/lossy archive.
    if not export.verified or (export.verification or {}).get("status") != "PASS":
        raise HTTPException(
            status_code=409,
            detail={
                "error": "export_not_verified",
                "message": (
                    "Source archive is incomplete or inconsistent. Import is "
                    "disabled until the source archive is verified."
                ),
            },
        )

    # Build canonical archive if not already present
    from pathlib import Path
    export_dir = Path(export.export_dir)
    archive_dir = export_dir / "archive"
    if not archive_dir.exists():
        _ = build_canonical_archive(
            export_dir, archive_dir,
            {"id": export.chat_id, "title": export.chat_title, "type": export.chat_type},
        )

    # Create import job record
    job = ImportJob(
        source_export_id=export.id,
        target_account_id=payload.target_account_id,
        target_peer_id=payload.target_peer_id,
        message_limit=payload.message_limit,
        status=ImportJobStatus.QUEUED,
        options={
            "contact_identifier": payload.contact_identifier,
            "test_mode": False,
        },
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    # Dispatch the Celery task so the worker actually runs the import
    from app.workers.import_tasks import run_import
    run_import.delay(job.id)

    return ImportJobPublic(
        id=job.id,
        source_export_id=job.source_export_id,
        target_account_id=job.target_account_id,
        target_peer_id=job.target_peer_id,
        message_limit=job.message_limit,
        status=job.status,
        options=job.options,
        created_at=job.created_at,
    )


@router.get("/{account_id}/target-chats", response_model=TargetChatsResponse)
async def list_target_chats(
    account_id: int,
    db: DbSession,
    user: Annotated[UserAccount, Depends(get_current_user)],
    manager: Manager,
    q: str = "",
):
    """Get candidate peers for a target Telegram account.

    Merges:
      - active dialogs (existing conversations), and
      - the full contacts list (so peers WITHOUT an open conversation are found),
    then filters by `q` across title / numeric id / username / phone.
    """
    account = await _get_owned_account(account_id, db, user)
    if account.status != "active":
        raise HTTPException(status_code=400, detail="Account is not logged in")

    # Acquire with a hard timeout; a zombied pooled client (dead connection
    # after network change / rebuild) otherwise blocks the per-account lock
    # forever and every Step 3 load hangs. On timeout, drop the cached client
    # and retry once with a fresh connection.
    try:
        client, release = await asyncio.wait_for(
            manager.acquire_client(account), timeout=15
        )
    except TimeoutError:
        await manager.drop(account_id)
        client, release = await manager.acquire_client(account)

    async def _dialogs() -> list:
        return await asyncio.wait_for(
            client.get_dialogs(limit=None), timeout=45
        )

    async def _contacts() -> list:
        from telethon import functions

        res = await asyncio.wait_for(
            client(functions.contacts.GetContactsRequest(hash=0)), timeout=30
        )
        return list(res.contacts)

    try:
        try:
            dialogs, contacts = await asyncio.gather(_dialogs(), _contacts())
        except TimeoutError:
            # Client is unresponsive — evict and reconnect once.
            await release()
            await manager.drop(account_id)
            client, release = await manager.acquire_client(account)
            dialogs, contacts = await asyncio.gather(_dialogs(), _contacts())

        ql = q.strip().lower()
        seen: dict[int, TargetChat] = {}

        def _build(entity, dialog=None):
            cid = int(getattr(entity, "id", 0))
            peer_id = getattr(dialog, "id", None) or cid
            access_hash = getattr(dialog, "access_hash", None)
            if access_hash is None:
                access_hash = getattr(entity, "access_hash", None)
            title = (getattr(entity, "title", None)
                     or getattr(entity, "first_name", None) or "")
            last = getattr(entity, "last_name", None)
            if last:
                title = f"{title} {last}".strip()
            phone = getattr(entity, "phone", None)
            if phone:
                phone = f"+{phone}"
            return TargetChat(
                id=cid,
                title=title or None,
                username=getattr(entity, "username", None),
                phone=phone,
                type=type(entity).__name__,
                peer_id=peer_id,
                access_hash=access_hash,
                message_count=getattr(dialog, "unread_count", 0) if dialog else 0,
                is_marked_unread=bool(getattr(dialog, "unread_mark", False)) if dialog else False,
            )

        def _matches(tc: TargetChat) -> bool:
            if not ql:
                return True
            return (ql in (tc.title or "").lower()
                    or ql in (tc.username or "").lower()
                    or ql in (tc.phone or "")
                    or ql in str(tc.id))

        # Only peers that can actually RECEIVE a history import are valid
        # targets (official API + tdlib can_import_messages):
        #   - private chats with a mutual contact (User entities)
        #   - supergroups (megagroups) where we hold rights
        # Broadcast channels and basic groups are excluded.
        def _importable(entity) -> bool:
            if type(entity).__name__ == "User":
                return True  # private chat (contact status checked at import)
            if getattr(entity, "broadcast", False):
                return False  # channel — not importable
            if getattr(entity, "megagroup", False):
                return True  # supergroup — importable
            return False     # basic group / other — not importable

        # Dialogs (with real peer access_hash for later import)
        for dialog in dialogs:
            entity = dialog.entity
            if not entity or not _importable(entity):
                continue
            tc = _build(entity, dialog)
            if _matches(tc):
                seen[tc.id] = tc

        # Contacts (peers without an open dialog still selectable)
        for entity in contacts:
            if not _importable(entity):
                continue
            if int(getattr(entity, "id", 0) or 0) <= 0:
                continue  # placeholder/incomplete contact entity
            tc = _build(entity)
            if _matches(tc):
                seen.setdefault(tc.id, tc)

        chats = sorted(seen.values(), key=lambda c: (c.title or "").lower())
        return TargetChatsResponse(chats=chats)
    finally:
        await release()
