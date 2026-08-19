"""Workers package — Celery application. Tasks arrive in Phase 3+."""
from app.workers.celery_app import celery_app

__all__ = ["celery_app"]
