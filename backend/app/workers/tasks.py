"""Celery tasks (export engine wiring).

The module-level async engine/pool in ``app.database`` is bound to whatever
event loop first uses it. Celery prefork workers run each task under a fresh
``asyncio.run`` loop, so reusing the global engine across tasks raises
"Future attached to a different loop". We therefore create a **task-local**
engine + session factory for every run and dispose it before exiting.
"""
import asyncio
import logging

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings
from app.services.export_engine import ExportEngine
from app.services.session_manager import SessionManager
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


def _build_engine() -> tuple[ExportEngine, object]:
    settings = get_settings()
    engine = create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        pool_timeout=60,
        echo=False,
    )
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    manager = SessionManager(settings, redis=redis)
    return ExportEngine(settings, session_factory, manager, redis=redis), engine


@celery_app.task(name="export.run", max_retries=0)
def run_export(export_id: int) -> str:
    """Celery entrypoint: run (or resume) one export job."""

    async def _run() -> str:
        export_engine, db_engine = _build_engine()
        try:
            await export_engine.run(export_id)
        finally:
            await db_engine.dispose()
        return f"export {export_id} finished"

    asyncio.run(_run())
