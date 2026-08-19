"""Fake Telethon client + factory for hermetic tests.

Mirrors the small Telethon surface the SessionManager uses:
connect/send_code_request/sign_in/get_me/disconnect and `session.save()`.
"""
from __future__ import annotations

from datetime import UTC, datetime

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


# ---------------------------------------------------------------------------
# Export engine fakes (message objects, dialogs, media descriptors)
# ---------------------------------------------------------------------------


class FakeSender:
    def __init__(self, id, first_name=None, last_name=None, username=None):
        self.id = id
        self.first_name = first_name
        self.last_name = last_name or ""
        self.username = username


class FakeChatEntity:
    def __init__(self, id, title, username=None, kind="group"):
        self.id = id
        self.title = title
        self.username = username
        # kind: group | channel | user — mirrors Telethon entity class names
        self._kind = kind

    @property
    def access_hash(self):  # noqa: D102
        return 99_000_000

    def __repr__(self):  # pragma: no cover
        return f"FakeChatEntity(id={self.id}, title={self.title!r})"


class FakeDialog:
    def __init__(self, entity, unread_count=0):
        self.entity = entity
        self.unread_count = unread_count


class FakeMessagesList(list):
    def __init__(self, items, total=None):
        super().__init__(items)
        self.total = total if total is not None else len(items)


def fake_message(
    id,
    text="",
    sender=None,
    date=None,
    edit_date=None,
    entities=None,
    reply_to_id=None,
    forward=None,
    reactions=None,
    views=None,
    media=None,
):
    """Build a fake Telethon message object with only the attributes the
    normalization layer reads."""
    import types

    msg = types.SimpleNamespace(
        id=id,
        date=date or datetime(2024, 1, 1, tzinfo=UTC),
        edit_date=edit_date,
        sender=sender,
        message=text,
        entities=entities or [],
        reply_to=types.SimpleNamespace(reply_to_msg_id=reply_to_id) if reply_to_id else None,
        forward=forward,
        reactions=reactions,
        views=views,
        forwards=None,
        media=media,
    )
    return msg


def fake_reactions(items):
    """items: list of (emoji, count)"""

    class _Reaction:  # emoji reaction
        emoticon = None

    class _Result:
        def __init__(self, reaction, count):
            self.reaction = reaction
            self.count = count

    class _Reactions:
        def __init__(self, results):
            self.results = results

    results = []
    for emoji, count in items:
        r = _Reaction()
        r.emoticon = emoji
        results.append(_Result(r, count))
    return _Reactions(results)


def fake_photo_media():
    """MessageMediaPhoto-shaped object."""

    class Photo:
        size = 2048

    class MediaPhoto:  # name matches classify_media's type check
        pass

    media = MediaPhoto()
    media.photo = Photo()
    media.date = datetime(2024, 1, 1, tzinfo=UTC)
    return media


def fake_document_media(mime_type="application/pdf", size=1024, attrs=(), filename=None):
    """MessageMediaDocument-shaped object."""

    class Document:
        pass

    class Attr:  # generic attribute; real names chosen by the caller
        pass

    class MediaDocument:  # name matches classify_media's type check
        pass

    doc = Document()
    doc.mime_type = mime_type
    doc.size = size
    doc.attributes = list(attrs)
    media = MediaDocument()
    media.document = doc
    if filename is not None:
        attr = Attr()
        attr.file_name = filename
        attr.voice = False
        doc.attributes = doc.attributes + [attr]
    return media


class FakeExportClient(FakeTelegramClient):
    """Telegram client fake with message history, dialogs and entities.

    Messages are stored newest-first (descending ids) exactly like Telethon
    returns them; get_messages implements offset_id pagination.
    """

    def __init__(self, api_id=1, api_hash="x" * 32, messages=None, dialogs=None, total=None):
        super().__init__(api_id, api_hash)
        self.messages = list(messages or [])
        self._dialogs = list(dialogs or [])
        self.entities_by_id: dict[int, object] = {}
        for m in self.messages:
            if isinstance(m, FakeChatEntity):
                self.entities_by_id[m.id] = m
        for d in self._dialogs:
            e = d.entity if hasattr(d, "entity") else d
            self.entities_by_id[getattr(e, "id", 0)] = e
        self._total = total if total is not None else len(self.messages)

    async def get_messages(self, entity, limit=0, offset_id=0):  # noqa: ARG002
        self.calls.append(("get_messages", limit, offset_id))
        if limit == 0:
            return FakeMessagesList([], total=self._total)
        # Telethon semantics: return messages with id < offset_id, descending,
        # capped by limit.
        result = [m for m in self.messages if m.id < offset_id] if offset_id else list(self.messages)
        return FakeMessagesList(result[:limit], total=self._total)

    async def get_entity(self, query):
        self.calls.append(("get_entity", query))
        if query is None:
            raise ValueError("no entity")
        if isinstance(query, int):
            entity = self.entities_by_id.get(query)
            if entity is not None:
                return entity
            for m in self.messages:
                if getattr(m, "id", None) == query and isinstance(m, FakeChatEntity):
                    return m
            raise ValueError(f"no entity for id {query}")
        q = str(query).lstrip("@").lower()
        for entity in self.entities_by_id.values():
            if (getattr(entity, "username", None) or "").lower() == q:
                return entity
        raise ValueError(f"no entity for {query!r}")

    async def get_input_entity(self, entity):
        return entity

    async def get_dialogs(self, limit=None):  # noqa: ARG002
        self.calls.append("get_dialogs")
        return FakeMessagesList(self._dialogs or [FakeDialog(e) for e in self.messages if isinstance(e, FakeChatEntity)])


class FakeExportFactory(FakeClientFactory):
    """Factory producing FakeExportClient instances with the given history."""

    def __init__(self, messages=None, dialogs=None, total=None, behaviors=None):
        super().__init__(behaviors or {})
        self.messages = messages
        self.dialogs = dialogs
        self.total = total

    def __call__(self, api_id, api_hash, session_string=None):
        client = FakeExportClient(
            api_id, api_hash,
            messages=self.messages,
            dialogs=self.dialogs,
            total=self.total,
        )
        self.clients.append(client)
        return client
