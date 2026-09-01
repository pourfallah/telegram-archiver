"""Hermetic test fixtures + a fake Telethon client.

We build real ``telethon.tl.types`` objects so the canonical archive builder
and raw snapshot serializer run against the same shapes production sees. The
fake client stands in for the MTProto transport and returns canned history,
reactions, and media — letting the whole engine run offline.
"""
from __future__ import annotations

from datetime import timezone, datetime, timedelta
from typing import Any

from telethon.tl import types as t

UTCNOW = datetime.now(timezone.utc)


def dt(offset_hours: float = 0.0):
    return UTCNOW + timedelta(hours=offset_hours)


def peer_user(uid: int) -> t.PeerUser:
    return t.PeerUser(uid)


def doc(oid: int, mime: str, attrs, size: int = 123) -> t.Document:
    return t.Document(id=oid, access_hash=oid * 7, file_reference=f"ref{oid}".encode(),
                      date=dt(), mime_type=mime, size=size, dc_id=1, attributes=attrs)


def message(msg_id: int, text: str = "", peer_id: t.PeerUser = None, from_uid: int = 100,
            date=None, media=None, entities=None, reply_to=None, fwd=None,
            grouped_id=None, reactions=None, out=False) -> t.Message:
    return t.Message(
        id=msg_id, peer_id=peer_id or peer_user(500),
        date=date or dt(float(-msg_id)), message=text, out=out,
        from_id=peer_user(from_uid), media=media, entities=entities or [],
        reply_to=reply_to, fwd_from=fwd, grouped_id=grouped_id,
        reactions=reactions)


def photo_message(msg_id: int, photo: t.Photo, text: str = "", date=None,
                  grouped_id=None, from_uid=100) -> t.Message:
    return message(msg_id, text=text, date=date, media=t.MessageMediaPhoto(photo=photo),
                   grouped_id=grouped_id, from_uid=from_uid)


def doc_message(msg_id: int, d: t.Document, text: str = "", media_ctor=None,
                date=None, grouped_id=None, from_uid=100) -> t.Message:
    mmedia = media_ctor or t.MessageMediaDocument(document=d)
    return message(msg_id, text=text, date=date, media=mmedia,
                   grouped_id=grouped_id, from_uid=from_uid)


def reaction_counts(*pairs) -> t.MessageReactions:
    """pairs: (reaction TL object, count)"""
    return t.MessageReactions(results=[
        t.ReactionCount(reaction=r, count=c) for r, c in pairs])


def _updates():
    return t.Updates(updates=[], users=[], chats=[], date=UTCNOW, seq=0)


class FakeResultChannel:
    """messages.ChannelMessages-like container for history results."""
    def __init__(self, messages: list):
        self.messages = messages


class FakeHistoryResult:
    """messages.messages -like container for getHistory."""
    def __init__(self, messages: list):
        self.messages = messages


class FakeGetMessagesReactionsResult:
    def __init__(self, updates: list):
        self.updates = updates


class FakeClient:
    """Dispatches MTProto requests to canned responses."""

    def __init__(self, history: list[t.Message] | None = None,
                 reactors=None, reaction_updates=None, download_bytes=b"FAKEPNG",
                 import_id: int = 424242) -> None:
        self.history = list(history or [])
        self.reactors = reactors or {}
        self.reaction_updates = reaction_updates or {}
        self.download_bytes = download_bytes
        self.import_id = import_id
        self.calls: list[str] = []
        self.uploaded: list[tuple[str, bytes]] = []
        self.me = t.User(id=100, is_self=True, first_name="A", username="a")
        self.connected = False

    async def connect(self): self.connected = True
    async def disconnect(self): self.connected = False

    async def get_me(self): return self.me

    async def get_input_entity(self, peer): return t.InputPeerUser(user_id=500, access_hash=9)

    async def __call__(self, request, *args, **kwargs):
        name = type(request).__name__
        self.calls.append(name)
        if name == "GetHistoryRequest":
            return FakeHistoryResult(self.history[: getattr(request, "limit", 500)])
        if name == "GetMessageReactionsListRequest":
            return _ReactorList(self.reactors.get(request.id, []))
        if name == "GetMessagesReactionsRequest":
            return FakeGetMessagesReactionsResult(
                self.reaction_updates.get(tuple(request.id), []))
        if name == "DeleteHistoryRequest":
            self.history = []
            return _updates()
        if name == "CheckHistoryImportRequest":
            return _DummyImport()
        if name == "CheckHistoryImportPeerRequest":
            return _DummyImport()
        if name == "InitHistoryImportRequest":
            return _ImportId(self.import_id)
        if name == "UploadImportedMediaRequest":
            return _DummyImport()
        if name == "StartHistoryImportRequest":
            return _updates()
        if name == "SendReactionRequest":
            return _updates()
        return _DummyImport()

    async def upload_file(self, data, file_name=None):
        self.uploaded.append((file_name, data))
        return t.InputFile(id=1, parts=1, name=file_name or "f", md5_checksum="0" * 32)

    async def iter_download(self, media, **kw):
        # Deterministic unique bytes per media object id (so distinct media
        # get distinct hashes — mirrors real distinct file contents).
        img = getattr(media, "photo", None) or getattr(media, "document", None)
        oid = getattr(img, "id", 0)
        yield f"MEDIA{oid}".encode() if oid else self.download_bytes

    def set_history(self, msgs): self.history = list(msgs)


class FakeRecoveryClient:
    """Minimal RecoveryClient substitute (avoids real Telethon client)."""

    def __init__(self, client: FakeClient, my_id: int = 100):
        self.client = client
        self._me = client.me
        self.api_id = 1
        self.api_hash = "x"
        self.phone = f"+1{my_id}"
        self.my_id_value = my_id

    @property
    def me(self): return self._me
    @property
    def my_id(self): return self.my_id_value

    async def connect(self, session=None): await self.client.connect()
    async def close(self): await self.client.disconnect()
    async def get_peer(self, peer): return await self.client.get_input_entity(peer)
    async def call(self, request, *a, **k): return await self.client(request, *a, **k)
    async def iter_download(self, media, *a, **k):
        async for chunk in self.client.iter_download(media, *a, **k):
            yield chunk
    def describe(self): return {"actor_id": self.my_id_value, "name": "Actor"}


# --- internal response stubs ---------------------------------------------
class _ReactorList:
    def __init__(self, reactions):
        self.reactions = reactions


class _DummyImport:
    __slots__ = ("ok",)
    def __init__(self):
        self.ok = True


class _ImportId:
    def __init__(self, iid):
        self.id = iid