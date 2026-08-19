"""Fake Telethon client + factory for hermetic tests.

Mirrors the small Telethon surface the SessionManager uses:
connect/send_code_request/sign_in/get_me/disconnect and `session.save()`.
"""
from __future__ import annotations

from telethon.errors import (
    ApiIdInvalidError,
    PasswordHashInvalidError,
    PhoneCodeInvalidError,
    PhoneNumberInvalidError,
    SessionPasswordNeededError,
)

DEFAULT_CODE = "12345"
DEFAULT_2FA = "secret-pass"


class FakeUser:
    id = 123456789
    first_name = "Fake"
    last_name = "User"
    username = "fakeuser"
    phone = "+12345678901"
    premium = False
    restricted = False


class FakeSession:
    def __init__(self, value: str) -> None:
        self.value = value

    def save(self) -> str:
        return self.value


class FakeTelegramClient:
    def __init__(
        self,
        api_id: int,
        api_hash: str,
        session_string: str | None = None,
        behavior: dict | None = None,
    ) -> None:
        self.api_id = api_id
        self.api_hash = api_hash
        behavior = behavior or {}
        self.needs_2fa = bool(behavior.get("needs_2fa", False))
        self.expected_code = str(behavior.get("code", DEFAULT_CODE))
        self.expected_2fa = str(behavior.get("2fa_password", DEFAULT_2FA))
        self.invalid_phone = bool(behavior.get("invalid_phone", False))
        self.invalid_api = bool(behavior.get("invalid_api", False))
        self.session = FakeSession(session_string or f"fake-session-{api_id}")
        self.connected = False
        self.calls: list = []

    async def connect(self) -> None:
        self.connected = True
        self.calls.append("connect")

    async def disconnect(self) -> None:
        self.connected = False
        self.calls.append("disconnect")

    async def send_code_request(self, phone: str, force_sms: bool = False):  # noqa: ARG002
        self.calls.append(("send_code_request", phone))
        if self.invalid_api:
            raise ApiIdInvalidError(request=None)
        if self.invalid_phone:
            raise PhoneNumberInvalidError(request=None)
        return None

    async def sign_in(self, phone=None, code=None, password=None):
        self.calls.append(("sign_in", phone, code, password is not None))
        if password is not None:
            if password != self.expected_2fa:
                raise PasswordHashInvalidError(request=None)
            return FakeUser()
        if code != self.expected_code:
            raise PhoneCodeInvalidError(request=None)
        if self.needs_2fa:
            raise SessionPasswordNeededError(request=None)
        return FakeUser()

    async def get_me(self):
        self.calls.append("get_me")
        return FakeUser()

    async def is_connected(self) -> bool:
        return self.connected


class FakeClientFactory:
    """Builds FakeTelegramClient instances; behavior keyed by api_id."""

    def __init__(self, behaviors: dict[int, dict] | None = None) -> None:
        self.behaviors = behaviors or {}
        self.clients: list[FakeTelegramClient] = []

    def __call__(self, api_id: int, api_hash: str, session_string: str | None = None):
        behavior = self.behaviors.get(api_id, {})
        client = FakeTelegramClient(api_id, api_hash, session_string, behavior)
        self.clients.append(client)
        return client
