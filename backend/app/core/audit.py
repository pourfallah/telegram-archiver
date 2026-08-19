"""Audit logging middleware — records every mutating API call.

Sensitive request data (OTP codes, passwords, api_hashes, session blobs)
is redacted before anything hits the database.
"""
import asyncio
import logging
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.core.crypto import decode_access_token
from app.models import AuditLog

logger = logging.getLogger(__name__)

SENSITIVE_KEYS = {"password", "code", "api_hash", "pin", "session", "token", "secret"}

_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _redact(value: Any, key: str) -> Any:
    if key.lower() in SENSITIVE_KEYS:
        return "[REDACTED]"
    if isinstance(value, dict):
        return {k: _redact(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v, key) for v in value]
    return value


class AuditMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, session_factory) -> None:
        super().__init__(app)
        self._session_factory = session_factory
        self._pending: set[asyncio.Task] = set()

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if request.method in _MUTATING_METHODS:
            task = asyncio.create_task(self._record(request, response.status_code))
            self._pending.add(task)
            task.add_done_callback(self._pending.discard)
        return response

    async def _record(self, request: Request, status_code: int) -> None:
        # Prefer the factory bound to the app (tests override it); fall back
        # to the one captured at construction (production).
        factory = getattr(request.app.state, "session_factory", None) or self._session_factory
        route = request.scope.get("route")
        route_name = getattr(route, "name", None) or "unknown"
        action = f"{request.method.lower()}.{route_name}"

        query = _redact(dict(request.query_params), "query")
        detail = {
            "path": request.url.path,
            "method": request.method,
            "query": query,
            "status": status_code,
        }

        user_id = getattr(request.state, "audit_user_id", None)
        if user_id is None:
            auth = request.headers.get("authorization", "")
            if auth.lower().startswith("bearer "):
                try:
                    user_id = decode_access_token(auth[7:])
                except ValueError as exc:
                    logger.warning("audit: token decode failed for %r: %s", auth[:40], exc)
                    user_id = None  # expired/invalid token — record as anonymous

        resource_id = request.path_params.get("id") or request.path_params.get("account_id")
        try:
            async with factory() as session:
                session.add(
                    AuditLog(
                        user_account_id=user_id,
                        action=action,
                        resource_type=route_name,
                        resource_id=str(resource_id) if resource_id is not None else None,
                        detail=detail,
                        ip=request.client.host if request.client else None,
                    )
                )
                await session.commit()
        except Exception:  # pragma: no cover - audit must never break the API
            logger.exception("Failed to write audit log entry")
