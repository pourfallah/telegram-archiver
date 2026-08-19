"""Aggregated API router."""
from fastapi import APIRouter

from app.api import accounts, auth, health, stats

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(stats.router)
api_router.include_router(auth.router)
api_router.include_router(accounts.router)
