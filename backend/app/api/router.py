"""Aggregated API router."""
from fastapi import APIRouter

from app.api import health, stats

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(stats.router)
