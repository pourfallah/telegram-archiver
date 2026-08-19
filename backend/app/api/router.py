"""Aggregated API router."""
from fastapi import APIRouter

from app.api import accounts, accounts_exports, auth, exports, health, imports, migrations, stats

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(stats.router)
api_router.include_router(auth.router)
api_router.include_router(accounts.router)
api_router.include_router(accounts_exports.router)
api_router.include_router(exports.router)
api_router.include_router(imports.router)
api_router.include_router(migrations.router)
