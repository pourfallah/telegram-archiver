"""Task dispatch abstraction.

Production uses Celery (worker container); tests use an in-process runner so
the whole pipeline can be exercised hermetically.
"""
from __future__ import annotations

import asyncio
from typing import Protocol

from app.services.export_engine import ExportEngine


class TaskRunner(Protocol):
    def enqueue(self, job_id: int):
        """Dispatch a job by id. Returns immediately."""


class CeleryTaskRunner:
    def __init__(self, celery_app, task_name: str = "export.run") -> None:
        self._celery = celery_app
        self._task_name = task_name

    def enqueue(self, job_id: int):
        self._celery.send_task(self._task_name, args=[job_id])


class InlineTaskRunner:
    """Runs exports in the current event loop — used by tests."""

    def __init__(self, engine: ExportEngine) -> None:
        self._engine = engine
        self.tasks: set[asyncio.Task] = set()

    def enqueue(self, job_id: int):
        task = asyncio.create_task(self._engine.run(job_id))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task
