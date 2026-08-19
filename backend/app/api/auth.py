"""Dashboard login (JWT issuance)."""
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.crypto import create_access_token, verify_password
from app.database import get_session
from app.models import UserAccount
from app.schemas.auth import LoginRequest, LoginResponse, UserSummary
from app.services.rate_limit import FixedWindowLimiter

router = APIRouter(tags=["auth"])

DbSession = Annotated[AsyncSession, Depends(get_session)]


def _login_limiter(request: Request) -> FixedWindowLimiter:
    return request.app.state.login_limiter


@router.post("/api/auth/login", response_model=LoginResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    db: DbSession,
    limiter: Annotated[FixedWindowLimiter, Depends(_login_limiter)],
) -> LoginResponse:
    await limiter.check(request.client.host if request.client else "unknown")

    user = await db.scalar(
        select(UserAccount).where(func.lower(UserAccount.email) == payload.email.lower())
    )
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is disabled",
        )

    settings = get_settings()
    token = create_access_token(user.id)
    user.last_login_at = datetime.now(UTC)
    request.state.audit_user_id = user.id  # token is created by this request itself
    await db.commit()

    return LoginResponse(
        access_token=token,
        expires_in=settings.jwt_expire_minutes * 60,
        user=UserSummary(id=user.id, email=user.email, is_admin=user.is_admin),
    )
