"""Telegram account management + login flow (phone -> OTP -> 2FA)."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session_manager
from app.core.crypto import encrypt_text
from app.core.security import get_current_user
from app.database import get_session
from app.models import TelegramSession, UserAccount
from app.schemas.account import (
    AccountCreate,
    AccountPublic,
    AccountStatusReport,
    CodeSubmit,
    TwoFASubmit,
)
from app.services.session_manager import LoginFlowError, SessionManager

router = APIRouter(
    prefix="/api/accounts",
    tags=["accounts"],
    dependencies=[Depends(get_current_user)],
)

DbSession = Annotated[AsyncSession, Depends(get_session)]
Manager = Annotated[SessionManager, Depends(get_session_manager)]


def _http_from_flow_error(exc: LoginFlowError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"error": exc.error_code, "message": exc.message},
    )


def _to_public(row: TelegramSession) -> AccountPublic:
    return AccountPublic(
        id=row.id,
        phone=row.phone,
        status=row.status,
        last_error=row.last_error,
        last_checked_at=row.last_checked_at,
        created_at=row.created_at,
    )


async def _get_account_or_404(
    account_id: int, db: AsyncSession, user: UserAccount
) -> TelegramSession:
    row = await db.scalar(
        select(TelegramSession).where(
            TelegramSession.id == account_id,
            TelegramSession.user_account_id == user.id,
        )
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
    return row


@router.get("", response_model=list[AccountPublic])
async def list_accounts(db: DbSession, user: Annotated[UserAccount, Depends(get_current_user)]):
    rows = (
        await db.scalars(
            select(TelegramSession)
            .where(TelegramSession.user_account_id == user.id)
            .order_by(TelegramSession.created_at.desc())
        )
    ).all()
    return [_to_public(r) for r in rows]


@router.post("", response_model=AccountPublic, status_code=status.HTTP_201_CREATED)
async def create_account(
    payload: AccountCreate,
    request: Request,
    db: DbSession,
    user: Annotated[UserAccount, Depends(get_current_user)],
    manager: Manager,
):
    existing = await db.scalar(
        select(TelegramSession).where(
            TelegramSession.user_account_id == user.id,
            TelegramSession.phone == payload.phone,
        )
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "duplicate_phone", "message": "This phone is already registered for your account"},
        )

    row = TelegramSession(
        user_account_id=user.id,
        phone=payload.phone,
        api_id=payload.api_id,
        api_hash_encrypted=encrypt_text(payload.api_hash),
        status="new",
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)

    try:
        await manager.start_login(row)
    except LoginFlowError as exc:
        row.status = "error"
        row.last_error = exc.message
        await db.commit()
        raise _http_from_flow_error(exc) from exc

    await db.commit()
    return _to_public(row)


@router.get("/{account_id}", response_model=AccountPublic)
async def get_account(
    account_id: int,
    db: DbSession,
    user: Annotated[UserAccount, Depends(get_current_user)],
):
    return _to_public(await _get_account_or_404(account_id, db, user))


@router.post("/{account_id}/code", response_model=AccountPublic)
async def submit_code(
    account_id: int,
    payload: CodeSubmit,
    request: Request,
    db: DbSession,
    user: Annotated[UserAccount, Depends(get_current_user)],
    manager: Manager,
):
    row = await _get_account_or_404(account_id, db, user)
    if row.status == "active":
        raise HTTPException(status_code=400, detail={"error": "already_logged_in"})
    try:
        await manager.submit_code(row, payload.code)
    except LoginFlowError as exc:
        row.last_error = exc.message
        await db.commit()
        raise _http_from_flow_error(exc) from exc
    await db.commit()
    return _to_public(row)


@router.post("/{account_id}/2fa", response_model=AccountPublic)
async def submit_2fa(
    account_id: int,
    payload: TwoFASubmit,
    request: Request,
    db: DbSession,
    user: Annotated[UserAccount, Depends(get_current_user)],
    manager: Manager,
):
    row = await _get_account_or_404(account_id, db, user)
    if row.status == "active":
        raise HTTPException(status_code=400, detail={"error": "already_logged_in"})
    try:
        await manager.submit_2fa(row, payload.password)
    except LoginFlowError as exc:
        row.last_error = exc.message
        await db.commit()
        raise _http_from_flow_error(exc) from exc
    await db.commit()
    return _to_public(row)


@router.post("/{account_id}/check", response_model=AccountStatusReport)
async def check_account(
    account_id: int,
    request: Request,
    db: DbSession,
    user: Annotated[UserAccount, Depends(get_current_user)],
    manager: Manager,
):
    row = await _get_account_or_404(account_id, db, user)
    try:
        report = await manager.check_account(row)
    except LoginFlowError as exc:
        row.status = "error"
        row.last_error = exc.message
        await db.commit()
        raise _http_from_flow_error(exc) from exc
    await db.commit()
    return AccountStatusReport(**report)


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    account_id: int,
    request: Request,
    db: DbSession,
    user: Annotated[UserAccount, Depends(get_current_user)],
    manager: Manager,
):
    row = await _get_account_or_404(account_id, db, user)
    await manager.drop(account_id)
    await db.delete(row)
    await db.commit()
    return None
