"""Celery application — broker wiring.

Export, conversion and validation tasks are registered in later phases;
this module exists so the worker container can boot from Phase 1.
"""
from celery import Celery

from app.config import get_settings

settings = get_settings()

celery_app = Celery(
    "telegram_archiver",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    # Long-running export tasks: acknowledge late so a worker loss re-delivers,
    # and rely on DB checkpoints for crash recovery (see PROJECT_PLAN.md §9.2).
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)
