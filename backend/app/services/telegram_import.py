"""Direct Telegram MTProto history-import engine.

Implements Telegram's real import API (https://core.telegram.org/api/import)
over a connected Telethon client:

    messages.checkHistoryImport(import_head)            # parse export head
    messages.checkHistoryImportPeer(peer)               # peer eligibility + confirm text
    messages.initHistoryImport(peer, file, media_count) # get import id
    messages.uploadImportedMedia(peer, import_id, file_name, media)  # media -> token
    messages.startHistoryImport(peer, import_id)        # actually import

Target is an EXISTING Telegram peer (typically an existing 1-to-1 chat with a
mutual contact). NO "fresh account" assumption is made — eligibility is decided
by Telegram's own peer check.

Imported messages remain Telegram-imported messages (fwd_from + imported flag);
original message ids / reaction / edit history are NOT restored. Everything not
reimportable is preserved in the canonical archive for offline fidelity.

MEDIA TOKEN MECHANISM: per the API, media uploaded via ``uploadImportedMedia``
returns a ``MessageMedia`` token that must be spliced back into the import file
in place of each media reference. The exact binary splicing is Telegram-internal
and only verifiable against a real target account — see docs/IMPORT_PROTOCOL.md.
"""
from __future__ import annotations

import logging
from typing import Any

from telethon.tl.functions import messages
from telethon.tl.types import InputPeerChannel, InputPeerChat

logger = logging.getLogger(__name__)

KNOWN_ERRORS = {
    "USER_NOT_MUTUAL_CONTACT": "history import into a private chat requires the two "
    "accounts to be mutual contacts",
    "PEER_ID_INVALID": "the selected target peer could not be resolved",
    "IMPORT_FILE_INVALID": "Telegram rejected the import file format",
    "IMPORT_FORMAT_DATE_INVALID": "Telegram rejected the date format in the import file",
    "IMPORT_FORMAT_UNRECOGNIZED": "Telegram did not recognise the import file format",
    "PREVIOUS_CHAT_IMPORT_ACTIVE_WAIT": "a previous import is still active for this chat — wait and retry",
    "CHAT_ADMIN_REQUIRED": "you need admin rights (change_info) on this chat to import",
}


class ImportProtocolError(Exception):
    """A Telegram RPC error surfaced during history import."""

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(f"{error_code}: {message}")
        self.error_code = error_code
        self.message = message


def _peer_type(peer) -> str:
    if isinstance(peer, InputPeerChannel):
        return "CHANNEL"
    if isinstance(peer, InputPeerChat):
        return "GROUP"
    return "PRIVATE_USER"


class TelegramImporter:
    """Wrapper over the Telegram history-import methods for a connected client."""

    def __init__(self, client) -> None:
        # ``client`` is a Telethon TelegramClient; tests inject a callable fake.
        self.client = client

    async def _req(self, request):
        try:
            return await self.client(request)
        except Exception as exc:  # noqa: BLE001 — map RPC errors
            code = self._rpc_name(exc)
            if code in KNOWN_ERRORS:
                raise ImportProtocolError(code, KNOWN_ERRORS[code]) from exc
            if code:
                raise ImportProtocolError(code, str(getattr(exc, "message", exc))) from exc
            raise

    @staticmethod
    def _rpc_name(exc: Exception) -> str:
        name = getattr(exc, "name", None)
        if not name and getattr(exc, "message", None):
            msg = str(exc.message)
            name = msg.split(":")[0].strip() if ":" in msg else msg
        return name or ""

    # ------------------------------------------------------------- peering

    async def peer_info(self, peer, entity=None) -> dict[str, Any]:
        """Gather pre-flight bounds for a target peer (best-effort)."""
        info = {
            "peer_id": getattr(peer, "peer_id", None) or getattr(peer, "id", None),
            "peer_type": _peer_type(peer),
            "username": None,
            "title": None,
            "mutual_contact": None,
            "current_message_count": None,
        }
        try:
            ent = entity or await self.client.get_entity(peer)
            info["username"] = getattr(ent, "username", None)
            info["title"] = getattr(ent, "title", None) or getattr(ent, "first_name", None)
            info["mutual_contact"] = getattr(ent, "mutual_contact", None)
            total = await self._count_messages(peer)
            info["current_message_count"] = total
        except Exception:  # noqa: BLE001 — pre-flight is best-effort
            pass
        return info

    async def _count_messages(self, peer) -> int | None:
        try:
            res = await self.client.get_messages(peer, limit=0)
            total = getattr(res, "total", None)
            if total in (0, 2**31 - 1):
                return None
            return total
        except Exception:  # noqa: BLE001
            return None

    async def resolve_peer(self, identifier: str):
        """Resolve a contact identifier (username/phone/id) to (peer, entity)."""
        entity = await self.client.get_entity(identifier)
        peer = await self.client.get_input_entity(entity)
        return peer, entity

    # ------------------------------------------------------- import protocol

    async def check_history_import_peer(self, peer) -> dict[str, Any]:
        """messages.checkHistoryImportPeer — eligibility + confirm text."""
        res = await self._req(messages.CheckHistoryImportPeerRequest(peer=peer))
        return {"confirm_text": str(getattr(res, "confirm_text", "")), "ok": True}

    async def check_history_import(self, import_head: str) -> dict[str, Any]:
        """messages.checkHistoryImport — parse the first <=100 lines of the head."""
        res = await self._req(messages.CheckHistoryImportRequest(import_head=import_head))
        return {
            "pm": bool(getattr(res, "pm", False)),
            "group": bool(getattr(res, "group", False)),
            "title": getattr(res, "title", None),
        }

    async def init_history_import(self, peer, import_file, media_count: int) -> int | None:
        """messages.initHistoryImport — return the import id."""
        if not hasattr(import_file, "id"):
            import_file = await self.client.upload_file(file=import_file)
        res = await self._req(
            messages.InitHistoryImportRequest(
                peer=peer, file=import_file, media_count=media_count
            )
        )
        return getattr(res, "id", None)

    async def upload_imported_media(self, peer, import_id: int, file_name: str, media) -> Any:
        """messages.uploadImportedMedia — upload one media and return its token."""
        res = await self._req(
            messages.UploadImportedMediaRequest(
                peer=peer, import_id=import_id, file_name=file_name, media=media
            )
        )
        return res

    async def start_history_import(self, peer, import_id: int) -> bool:
        """messages.startHistoryImport — actually start the import."""
        res = await self._req(messages.StartHistoryImportRequest(peer=peer, import_id=import_id))
        return bool(res)
