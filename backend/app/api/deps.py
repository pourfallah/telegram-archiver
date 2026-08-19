"""Shared API dependencies."""
from fastapi import Request

from app.services.session_manager import SessionManager


def get_session_manager(request: Request) -> SessionManager:
    return request.app.state.session_manager
