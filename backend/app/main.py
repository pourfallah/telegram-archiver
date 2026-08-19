"""FastAPI application factory."""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from app import __version__
from app.api.router import api_router
from app.config import get_settings
from app.core.audit import AuditMiddleware
from app.core.crypto import hash_password
from app.database import async_session_factory
from app.models import UserAccount
from app.services.rate_limit import FixedWindowLimiter
from app.services.session_manager import SessionManager

logger = logging.getLogger("app")


async def _seed_admin() -> None:
    """Create the initial dashboard admin from env settings (idempotent)."""
    settings = get_settings()
    async with async_session_factory() as session:
        existing = await session.scalar(select(UserAccount).where(UserAccount.is_admin.is_(True)))
        if existing is not None:
            return
        session.add(
            UserAccount(
                email=settings.admin_email,
                password_hash=hash_password(settings.admin_password),
                is_admin=True,
                is_active=True,
            )
        )
        await session.commit()
        logger.info("Seeded admin user %s", settings.admin_email)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    # Runtime directories (exports volume, local sqlite data dir).
    settings.exports_dir.mkdir(parents=True, exist_ok=True)
    if settings.sqlite_file:
        Path(settings.database_url.split("///")[-1]).parent.mkdir(parents=True, exist_ok=True)

    redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        await redis_client.ping()
        logger.info("Connected to Redis at %s", settings.redis_url)
    except Exception:
        logger.warning(
            "Redis unreachable at %s — rate limiting and progress tracking degraded",
            settings.redis_url,
        )
        redis_client = None

    app.state.redis = redis_client
    app.state.session_manager = SessionManager(settings, redis=redis_client)
    app.state.login_limiter = FixedWindowLimiter(redis_client, 10, 300, "login")
    app.state.code_limiter = FixedWindowLimiter(redis_client, 10, 300, "code")

    if not settings.session_encryption_key:
        logger.warning(
            "SESSION_ENCRYPTION_KEY is empty — Telegram login will fail until it is set "
            "(see .env.example for generation instructions)."
        )

    await _seed_admin()

    yield

    await app.state.session_manager.shutdown()
    if redis_client is not None:
        await redis_client.aclose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(AuditMiddleware, session_factory=async_session_factory)

    app.include_router(api_router)
    return app


app = create_app()
