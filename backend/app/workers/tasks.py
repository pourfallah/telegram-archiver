"""Celery tasks (export engine wiring)."""
import asyncio
import logging

import redis.asyncio as aioredis

from app.config import get_settings
from app.database import async_session_factory
from app.services.export_engine import ExportEngine
from app.services.session_manager import SessionManager
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


def _build_engine() -> ExportEngine:
    settings = get_settings()
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    manager = SessionManager(settings, redis=redis)
    return ExportEngine(settings, async_session_factory, manager, redis=redis)


@celery_app.task(name="export.run", max_retries=0)
def run_export(export_id: int) -> str:
    """Celery entrypoint: run (or resume) one export job."""
    engine = _build_engine()
    asyncio.run(engine.run(export_id))
    return f"export {export_id} finished"
