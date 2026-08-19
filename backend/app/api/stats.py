"""Dashboard stats endpoint (Phase 1 stub — real aggregations land with later phases)."""
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_session
from app.models import ChatExport, TelegramSession

router = APIRouter(prefix="/api", tags=["stats"])

DbSession = Annotated[AsyncSession, Depends(get_session)]


@router.get("/stats")
async def stats(db: DbSession) -> dict:
    settings = get_settings()

    accounts = await db.scalar(select(func.count(TelegramSession.id))) or 0
    exports_total = await db.scalar(select(func.count(ChatExport.id))) or 0
    exports_running = (
        await db.scalar(
            select(func.count(ChatExport.id)).where(ChatExport.status.in_(["queued", "running"]))
        )
        or 0
    )

    # Storage usage: bytes on disk under the exports volume.
    storage_bytes = 0
    if settings.exports_dir.exists():
        for path in settings.exports_dir.rglob("*"):
            if path.is_file():
                storage_bytes += path.stat().st_size

    return {
        "accounts": accounts,
        "exports_total": exports_total,
        "exports_running": exports_running,
        "storage_bytes": storage_bytes,
    }
