"""Telegram session manager.

Responsibilities:
- run the interactive login flow (phone -> code -> optional 2FA) against
  Telegram's MTProto servers, keeping the in-progress Telethon client alive
  between HTTP requests (memory, mirrored to Redis for crash recovery);
- persist the finished session as a Fernet-encrypted string (never plaintext);
- pool connected clients for export/status work (LRU, bounded by
  MAX_CONCURRENT_SESSIONS), rehydrating from encrypted storage on demand.

Threading model: one asyncio loop per process (FastAPI app or Celery task);
all methods are async and must be awaited from the owning loop only.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC
from typing import Any, Protocol

from telethon import TelegramClient
from telethon.errors import (
    ApiIdInvalidError,
    FloodWaitError,
    PasswordHashInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberInvalidError,
    SessionPasswordNeededError,
)
from telethon.sessions import StringSession

from app import __version__
from app.config import Settings
from app.core.crypto import decrypt_text, encrypt_text
from app.models import TelegramSession

logger = logging.getLogger(__name__)

FLOW_REDIS_TTL_SECONDS = 600


class LoginFlowError(Exception):
    """Raised when a login step fails in a way that maps to an HTTP error."""

    def __init__(self, message: str, status_code: int = 400, error_code: str = "login_error") -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.error_code = error_code


class ClientFactory(Protocol):
    def __call__(self, api_id: int, api_hash: str, session_string: str | None = None) -> TelegramClient:
        """Build a (disconnected) Telethon client."""


def default_client_factory(api_id: int, api_hash: str, session_string: str | None = None) -> TelegramClient:
    session = StringSession(session_string) if session_string else StringSession()
    return TelegramClient(
        session,
        api_id,
        api_hash,
        device_model="Telegram Archive & Migration Suite",
        app_version=__version__,
        lang_code="en",
        system_lang_code="en",
    )


@dataclass
class HeldLogin:
    client: TelegramClient = field(repr=False)
    busy: asyncio.Lock = field(default_factory=asyncio.Lock)


@dataclass
class PoolEntry:
    client: TelegramClient = field(repr=False)
    last_used: float = 0.0


class SessionManager:
    def __init__(
        self,
        settings: Settings,
        redis=None,
        client_factory: ClientFactory | None = None,
    ) -> None:
        self._settings = settings
        self._redis = redis
        self._factory: ClientFactory = client_factory or default_client_factory
        self._flows: dict[int, HeldLogin] = {}
        self._pool: dict[int, PoolEntry] = {}
        self._account_locks: dict[int, asyncio.Lock] = {}
        self._lock = asyncio.Lock()

    # ---------------------------------------------------------------- login

    async def start_login(self, account: TelegramSession) -> None:
        """Send the SMS/call code request for a new account and record the step."""
        client = self._factory(account.api_id, decrypt_text(account.api_hash_encrypted))
        await client.connect()
        held = HeldLogin(client=client)
        self._flows[account.id] = held

        async with held.busy:
            try:
                await client.send_code_request(account.phone)
            except SessionPasswordNeededError as exc:
                raise LoginFlowError(
                    "This phone already has a 2FA password — submit it at the 2FA step.",
                    status_code=400,
                    error_code="password_needed",
                ) from exc
            except PhoneNumberInvalidError as exc:
                await self._abort_flow(account.id)
                raise LoginFlowError("Invalid phone number", error_code="invalid_phone") from exc
            except ApiIdInvalidError as exc:
                await self._abort_flow(account.id)
                raise LoginFlowError(
                    "Invalid api_id / api_hash — recheck your my.telegram.org credentials.",
                    error_code="invalid_api",
                ) from exc
            except FloodWaitError as exc:
                await self._abort_flow(account.id)
                raise LoginFlowError(
                    f"Telegram is throttling this number (try again in {exc.seconds}s)",
                    status_code=429,
                    error_code="flood_wait",
                ) from exc

        account.status = "auth_pending_code"
        await self._remember_flow(account, step="code")

    async def submit_code(self, account: TelegramSession, code: str) -> bool:
        """Submit the OTP code. Returns True when a 2FA password is required next."""
        held = self._flows.get(account.id)
        if held is None:
            # API restarted mid-flow: rehydrate and re-request the code.
            await self.start_login(account)
            held = self._flows[account.id]

        async with held.busy:
            try:
                await held.client.sign_in(code=code.strip())
            except SessionPasswordNeededError:
                account.status = "auth_pending_2fa"
                await self._remember_flow(account, step="2fa")
                return True
            except (PhoneCodeInvalidError, PhoneCodeExpiredError) as exc:
                raise LoginFlowError(
                    "Invalid or expired code — request a new one and try again",
                    error_code="invalid_code",
                ) from exc
            except FloodWaitError as exc:
                raise LoginFlowError(
                    f"Telegram is throttling this number (try again in {exc.seconds}s)",
                    status_code=429,
                    error_code="flood_wait",
                ) from exc

            await self._finalize(account, held.client)
        return False

    async def submit_2fa(self, account: TelegramSession, password: str) -> None:
        held = self._flows.get(account.id)
        if held is None:
            raise LoginFlowError(
                "Login flow expired — start over from the code step",
                status_code=400,
                error_code="flow_expired",
            )
        async with held.busy:
            try:
                await held.client.sign_in(password=password)
            except PasswordHashInvalidError as exc:
                raise LoginFlowError(
                    "Wrong 2FA password", error_code="wrong_2fa_password"
                ) from exc
            except FloodWaitError as exc:
                raise LoginFlowError(
                    f"Telegram is throttling this number (try again in {exc.seconds}s)",
                    status_code=429,
                    error_code="flood_wait",
                ) from exc
            await self._finalize(account, held.client)

    # ------------------------------------------------------------- check

    async def check_account(self, account: TelegramSession) -> dict[str, Any]:
        """Verify connectivity and account state; returns a status report."""
        if not account.session_encrypted:
            raise LoginFlowError(
                "Account is not logged in yet",
                status_code=400,
                error_code="not_authenticated",
            )
        client = await self._acquire(account)
        try:
            me = await client.get_me()
            restricted = bool(getattr(me, "restricted", False))
            account.status = "limited" if restricted else "active"
            account.last_checked_at = self._utcnow()
            return {
                "id": account.id,
                "status": account.status,
                "user": {
                    "id": getattr(me, "id", None),
                    "first_name": getattr(me, "first_name", None),
                    "last_name": getattr(me, "last_name", None),
                    "username": getattr(me, "username", None),
                    "phone": getattr(me, "phone", None),
                    "premium": bool(getattr(me, "premium", False)),
                    "restricted": restricted,
                },
            }
        finally:
            self._touch(account.id)

    # ------------------------------------------------------------ pooling

    async def acquire_client(self, account: TelegramSession):
        """Acquire a connected client for an account, serialized per account.

        Returns an awaitable context manager yielding the client — only one
        task can hold a given account's client at a time, so parallel exports
        from the same account cannot interleave requests.
        """
        async with self._lock:
            lock = self._account_locks.setdefault(account.id, asyncio.Lock())
        await lock.acquire()

        async def _release() -> None:
            lock.release()
            self._touch(account.id)

        try:
            client = await self._acquire(account)
        except BaseException:
            lock.release()
            raise
        return client, _release

    async def _acquire(self, account: TelegramSession) -> TelegramClient:
        """Return a connected client for an authenticated account."""
        async with self._lock:
            entry = self._pool.get(account.id)
            if entry is not None:
                entry.last_used = time.monotonic()
                return entry.client

        client = self._factory(
            account.api_id,
            decrypt_text(account.api_hash_encrypted),
            session_string=decrypt_text(account.session_encrypted),
        )
        await client.connect()

        async with self._lock:
            self._evict_if_needed(account.id)
            self._pool[account.id] = PoolEntry(client=client, last_used=time.monotonic())
        return client

    def _touch(self, account_id: int) -> None:
        entry = self._pool.get(account_id)
        if entry is not None:
            entry.last_used = time.monotonic()

    def _evict_if_needed(self, new_account_id: int) -> None:
        if len(self._pool) < self._settings.max_concurrent_sessions:
            return
        if new_account_id in self._pool:
            return
        victim_id = min(self._pool, key=lambda k: self._pool[k].last_used)
        victim = self._pool.pop(victim_id)
        asyncio.get_running_loop().create_task(self._disconnect_later(victim.client))

    async def _disconnect_later(self, client: TelegramClient) -> None:
        try:
            await client.disconnect()
        except Exception:  # noqa: BLE001 - best effort eviction
            logger.warning("Failed to disconnect evicted client", exc_info=True)

    async def drop(self, account_id: int) -> None:
        """Forget a client: called on account deletion or logout."""
        self._flows.pop(account_id, None)
        self._account_locks.pop(account_id, None)
        entry = self._pool.pop(account_id, None)
        if entry is not None:
            await self._disconnect_later(entry.client)
        if self._redis is not None:
            await self._redis.delete(f"login_flow:{account_id}")

    async def shutdown(self) -> None:
        """Disconnect every pooled client (app shutdown)."""
        while self._pool:
            _, entry = self._pool.popitem()
            await self._disconnect_later(entry.client)
        self._flows.clear()

    # ------------------------------------------------------------- finalize

    async def _finalize(self, account: TelegramSession, client: TelegramClient) -> None:
        me = await client.get_me()
        session_string = client.session.save()
        account.session_encrypted = encrypt_text(session_string)
        account.status = "active"
        account.last_checked_at = self._utcnow()
        account.last_error = None
        self._flows.pop(account.id, None)
        if self._redis is not None:
            await self._redis.delete(f"login_flow:{account.id}")
        async with self._lock:
            self._evict_if_needed(account.id)
            self._pool[account.id] = PoolEntry(client=client, last_used=time.monotonic())
        logger.info("Account %s logged in (user %s)", account.id, getattr(me, "username", "?"))

    async def _abort_flow(self, account_id: int) -> None:
        held = self._flows.pop(account_id, None)
        if held is not None:
            await self._disconnect_later(held.client)
        if self._redis is not None:
            await self._redis.delete(f"login_flow:{account_id}")

    async def _remember_flow(self, account: TelegramSession, step: str) -> None:
        if self._redis is None:
            return
        payload = {
            "phone": account.phone,
            "api_id": account.api_id,
            "api_hash_encrypted": account.api_hash_encrypted,
            "step": step,
        }
        await self._redis.set(
            f"login_flow:{account.id}",
            json.dumps(payload),
            ex=FLOW_REDIS_TTL_SECONDS,
        )

    @staticmethod
    def _utcnow():
        from datetime import datetime

        return datetime.now(UTC)
