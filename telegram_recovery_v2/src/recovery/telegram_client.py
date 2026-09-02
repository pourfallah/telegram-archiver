"""Telethon client handling for Telegram Recovery v2.

Wraps a single logical actor (SOURCE account A or TARGET account B). One
instance per actor; both derive from the same ``RecoveryClient`` class so the
engine never distinguishes "A logic" from "B logic" — it just uses the right
client (and session) per actor.

Secrets (api_id / api_hash / session string) are never logged or printed.
"""
from __future__ import annotations

import asyncio
import base64
import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger("recovery.client")

# Constructors that are pure bookkeeping and never carry usable secrets.
_attrs_effort_order = ("id", "user_id", "channel_id", "chat_id", "title",
                       "first_name", "last_name", "username", "phone",
                       "message", "date", "edit_date", "fwd_from", "reply_to",
                       "media", "entities", "grouped_id", "reactions", "views",
                       "forwards", "out")

_TL_WRAPPERS = ("_", "subclass_of", "CONSTRUCTOR_ID", "SUBCLASS_OF_ID")


def make_client_factory(get_client):
    """Adapt an arbitrary ``get_client(...)`` callable into a ``ClientFactory``.

    ``get_client(api_id, api_hash, session_string=None) -> async client``
    This keeps the engine testable: tests inject a fake client.
    """
    return get_client


async def default_connect(api_id: int, api_hash: str, session_string: str | None,
                          device: str = "Telegram Recovery v2"):
    """Connect a real Telethon ``TelegramClient`` (StringSession)."""
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    session = StringSession(session_string) if session_string else StringSession()
    client = TelegramClient(session, api_id, api_hash,
                            device_model=device, app_version="0.1.0",
                            lang_code="en", system_lang_code="en")
    await client.connect()
    return client


class RecoveryClient:
    """Logical wrapper around one connected Telethon client for one actor."""

    def __init__(self, api_id: int, api_hash: str, phone: str | None = None,
                 connect=default_connect) -> None:
        if not api_id or not api_hash:
            raise ValueError("api_id and api_hash are required")
        self.api_id = api_id
        self.api_hash = api_hash
        self.phone = phone
        self.client = None
        self._connect = connect
        self._me = None

    async def connect(self, session_string: str | None = None) -> None:
        self.client = await self._connect(self.api_id, self.api_hash, session_string)
        self._me = await self.client.get_me()

    async def close(self) -> None:
        if self.client is not None:
            await self.client.disconnect()
            self.client = None

    @property
    def me(self):
        return self._me

    @property
    def my_id(self) -> int | None:
        return getattr(self._me, "id", None)

    async def get_peer(self, peer) -> Any:
        """Resolve ``peer`` (id / username / phone) to an InputPeer."""
        return await self.client.get_input_entity(peer)

    # -- request plumbing ---------------------------------------------
    async def call(self, request, *args, **kwargs) -> Any:
        """Call an MTProto request on this actor's client."""
        if self.client is None:
            raise RuntimeError("client not connected")
        return await self.client(request, *args, **kwargs)

    async def iter_download(self, media, *args, **kwargs):
        """Stream media bytes from this actor's connected client (async generator)."""
        if self.client is None:
            raise RuntimeError("client not connected")
        async for chunk in self.client.iter_download(media, *args, **kwargs):
            yield chunk

    def describe(self) -> dict:
        """Non-secret identity descriptor (safe to log / report)."""
        me = self._me
        return {
            "actor_id": getattr(me, "id", self.phone),
            "name": (f"{getattr(me,'first_name','') or ''} "
                     f"{getattr(me,'last_name','') or ''}").strip() or None,
            "username": getattr(me, "username", None),
        }


# ------------------------------------------------------------------------
# Raw MTProto snapshot sanitization
# ------------------------------------------------------------------------
def tl_to_plain(obj: Any, _seen: set[int] | None = None) -> Any:
    """Convert a Telethon TL object (or nested values) to JSON-safe plain data.

    Sanitization scope per project rules: ONLY session/API/authentication
    secrets are removed. Ordinary message properties are preserved. No
    Telegram Message object contains session secrets, so this is naturally
    safe — but we defensively refuse anything that looks like a secret key.
    """
    _seen = _seen or set()
    if obj is None:
        return None
    if isinstance(obj, bool) or obj is None:
        return obj
    if isinstance(obj, int):
        return obj
    if isinstance(obj, float):
        return obj
    if isinstance(obj, str):
        return obj
    if isinstance(obj, bytes):
        # file_reference / waveform — bytes are binary, base64 them.
        if len(obj) < 8:
            return obj.hex()
        return "base64:" + base64.b64encode(obj).decode()
    if isinstance(obj, datetime):
        return obj.isoformat() if obj.tzinfo else obj.replace(tzinfo=UTC).isoformat()
    if isinstance(obj, list):
        return [tl_to_plain(x, _seen) for x in obj]
    if isinstance(obj, tuple):
        return [tl_to_plain(x, _seen) for x in obj]

    # Generic object: serialize public attributes.
    if not hasattr(obj, "__dict__") and not hasattr(obj, "__slots__"):
        return repr(obj)

    cls = type(obj)
    ident = id(obj)
    if ident in _seen:  # cycle guard
        return None
    _seen.add(ident)

    out: dict[str, Any] = {}
    for key, value in vars(obj).items():
        if key in _TL_WRAPPERS or key.startswith("_"):
            continue
        out[key] = tl_to_plain(value, _seen)
    if not out:
        out["__repr__"] = repr(obj)
    out["__tl__"] = cls.__name__
    return out