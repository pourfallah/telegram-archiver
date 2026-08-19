"""Health checks (liveness + database readiness)."""
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import __version__
from app.database import get_session

router = APIRouter(tags=["health"])

DbSession = Annotated[AsyncSession, Depends(get_session)]


@router.get("/health")
async def health(db: DbSession) -> dict:
    try:
        await db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False

    body = {
        "status": "ok" if db_ok else "degraded",
        "version": __version__,
        "db": "up" if db_ok else "down",
    }
    if not db_ok:
        # Let orchestrators/healthchecks treat a dead database as unhealthy.
        return JSONResponse(
            status_code=503,
            content=body,
        )
    return body
