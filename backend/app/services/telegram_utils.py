"""Telegram message/entity normalization helpers.

Turns raw Telethon message objects into the plain JSON-shaped dicts that the
export writers, the Postgres ledger, and the WhatsApp converter consume.
All functions here are pure and deterministic — the export engine relies on
them being safe to unit-test without a live connection.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

ENTITY_TYPE_MAP = {
    "MessageEntityBold": "bold",
    "MessageEntityItalic": "italic",
    "MessageEntityUnderline": "underline",
    "MessageEntityStrike": "strike",
    "MessageEntityCode": "code",
    "MessageEntityPre": "pre",
    "MessageEntityMention": "mention",
    "MessageEntityHashtag": "hashtag",
    "MessageEntityCashtag": "cashtag",
    "MessageEntityBotCommand": "bot_command",
    "MessageEntityUrl": "url",
    "MessageEntityEmail": "email",
    "MessageEntityPhone": "phone",
    "MessageEntityTextUrl": "text_url",
    "MessageEntityMentionName": "mention_name",
    "MessageEntityBlockquote": "blockquote",
    "MessageEntityUnknown": "unknown",
}

# Media type in which each Telegram entity prefix is classified.
MEDIA_ATTRIBUTE_MAP = {
    "DocumentAttributeVideo": "video",
    "DocumentAttributeAudio": "audio",
    "DocumentAttributeSticker": "sticker",
    "DocumentAttributeAnimated": "animation",
    "DocumentAttributeFilename": "document",
}

_SAFE_FILENAME = re.compile(r'[^A-Za-z0-9._ -]+')


def utcnow() -> datetime:
    return datetime.now(UTC)


def isoformat(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def safe_filename(name: str, fallback: str = "file") -> str:
    """Sanitize a user-controlled filename for the local filesystem.

    Keeps letters, digits, dot, underscore, space and dash; collapses runs of
    other characters; never returns empty or dot-only names.
    """
    cleaned = _SAFE_FILENAME.sub("_", name).strip(" .")
    if not cleaned or cleaned in {".", ".."} or len(cleaned) > 200:
        return fallback
    return cleaned


def sender_info(sender) -> dict[str, Any] | None:
    """Best-effort sender descriptor: id, name, username."""
    if sender is None:
        return None
    name = None
    for attr in ("first_name", "title", "username"):
        if hasattr(sender, attr):
            name = getattr(sender, attr)
            if name:
                break
    # Prefer "First Last" for users, plain title for chats.
    if hasattr(sender, "first_name"):
        first = getattr(sender, "first_name", "") or ""
        last = getattr(sender, "last_name", "") or ""
        name = (first + (" " + last if last else "")).strip() or name
    return {
        "id": getattr(sender, "id", None),
        "name": name,
        "username": getattr(sender, "username", None),
    }


def serialize_entities(message) -> list[dict[str, Any]]:
    """Flatten Telegram entity objects into plain dicts."""
    out = []
    for entity in getattr(message, "entities", None) or []:
        kind = ENTITY_TYPE_MAP.get(type(entity).__name__, type(entity).__name__)
        item: dict[str, Any] = {
            "type": kind,
            "offset": getattr(entity, "offset", 0),
            "length": getattr(entity, "length", 0),
        }
        for attr in ("url", "user_id", "language"):
            if hasattr(entity, attr) and getattr(entity, attr) is not None:
                item[attr] = getattr(entity, attr)
        out.append(item)
    return out


def serialize_forward(message) -> dict[str, Any] | None:
    fwd = getattr(message, "forward", None)
    if fwd is None:
        return None
    name = None
    origin = getattr(fwd, "from_id", None)
    origin_id = None
    if origin is not None:
        origin_id = getattr(origin, "user_id", None) or getattr(origin, "channel_id", None) or getattr(origin, "chat_id", None)
    if getattr(fwd, "chat", None) is not None:
        name = fwd.chat.title or fwd.chat.first_name
    elif getattr(fwd, "sender", None) is not None:
        name = fwd.sender.first_name or fwd.sender.username
    return {
        "from_id": origin_id,
        "name": name,
        "date": isoformat(getattr(fwd, "date", None)),
    }


def serialize_reactions(message) -> dict[str, int] | None:
    reactions = getattr(message, "reactions", None)
    results = getattr(reactions, "results", None)
    if not results:
        return None
    out: dict[str, int] = {}
    for r in results:
        reaction = getattr(r, "reaction", None)
        if reaction is None:
            continue
        emoji = getattr(reaction, "emoticon", None) or str(reaction)
        out[emoji] = out.get(emoji, 0) + getattr(r, "count", 0)
    return out or None


def classify_media(message) -> list[dict[str, Any]]:
    """Describe every media payload attached to a message.

    Returns a list of dicts: {type, mime_type, size_bytes, original_filename,
    filename, ext} where ``filename``/``ext`` are safe, collision-free local
    names the downloader can use. Unknown media types are still captured so
    the export never silently drops data.
    """
    media = getattr(message, "media", None)
    if media is None:
        return []

    out: list[dict[str, Any]] = []
    media_type_name = type(media).__name__

    if media_type_name == "MessageMediaPhoto":
        photo = getattr(media, "photo", None)
        size = getattr(photo, "size", None) if photo is not None else None
        date = getattr(media, "date", None)
        out.append(
            _media_entry(
                type="photo",
                mime_type="image/jpeg",
                size_bytes=size,
                original_filename=f"photo_{date.strftime('%Y%m%d') if date else 'photo'}.jpg",
            )
        )

    elif media_type_name == "MessageMediaDocument":
        doc = getattr(media, "document", None)
        mime = getattr(doc, "mime_type", None) or "application/octet-stream"
        size = getattr(doc, "size", None)
        attrs = getattr(doc, "attributes", None) or []

        media_type = "document"
        original = None
        for attr in attrs:
            attr_kind = MEDIA_ATTRIBUTE_MAP.get(type(attr).__name__)
            if attr_kind in {"video", "animation", "sticker"}:
                media_type = attr_kind
            elif attr_kind == "audio":
                media_type = "voice" if getattr(attr, "voice", False) else "audio"
            if hasattr(attr, "file_name") and attr.file_name:
                original = attr.file_name

        if media_type == "video" and mime == "image/gif":
            media_type = "gif"
        if original is None:
            ext = mime.split("/")[-1].split(";")[0] or "bin"
            original = f"{media_type}_{size or 'file'}.{ext}"
        out.append(
            _media_entry(
                type=media_type,
                mime_type=mime,
                size_bytes=size,
                original_filename=original,
            )
        )

    elif media_type_name == "MessageMediaWebPage":
        # Web previews are not downloadable objects; capture nothing.
        pass

    elif media_type_name == "MessageMediaGeo":
        out.append(
            _media_entry(type="geo", mime_type="application/json", original_filename="location.json")
        )

    elif media_type_name == "MessageMediaContact":
        contact = getattr(media, "contact", None)
        out.append(
            _media_entry(
                type="contact",
                mime_type="text/vcard",
                original_filename="contact.vcf",
                extra={
                    "first_name": getattr(contact, "first_name", None),
                    "last_name": getattr(contact, "last_name", None),
                    "phone": getattr(contact, "phone_number", None),
                },
            )
        )

    else:
        out.append(
            _media_entry(
                type="unknown",
                mime_type="application/octet-stream",
                size_bytes=None,
                original_filename="unknown.bin",
            )
        )

    return out


def _media_entry(
    type: str,
    mime_type: str,
    size_bytes: int | None,
    original_filename: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ext = original_filename.rsplit(".", 1)[-1] if "." in original_filename else ""
    return {
        "type": type,
        "mime_type": mime_type,
        "size_bytes": size_bytes,
        "original_filename": original_filename,
        "filename": f"{type}_{size_bytes or 0}.{safe_filename(ext, 'bin')}",
        "ext": ext,
        "extra": extra,
    }


def serialize_input_peer(peer) -> dict[str, Any] | None:
    """Serialize a Telethon InputPeer into a plain JSON-safe dict.

    The access hash needed to re-resolve a chat after a restart lives inside
    the peer object; storing it lets the engine re-attach without re-searching
    dialogs.
    """
    if peer is None:
        return None
    name = type(peer).__name__
    if not name.startswith("InputPeer"):
        # Not an input peer — fall back to the plain id, resolvable only when
        # the entity is already in the client's session cache.
        return {"cls": "InputPeerEmpty", "id": getattr(peer, "id", None)}
    fields = {k: v for k, v in vars(peer).items() if v is not None}
    return {"cls": name, **fields}


def deserialize_input_peer(data: dict[str, Any] | None):
    """Rebuild an InputPeer from the dict produced by serialize_input_peer.

    Returns None for unknown/empty classes so callers can fall back to plain
    id resolution (get_entity(chat_id)) — which works when the entity is still
    in the client's session cache.
    """
    if not data or data.get("cls", "InputPeerEmpty") == "InputPeerEmpty":
        return None
    import telethon.tl.types as tl_types

    cls = getattr(tl_types, data["cls"], None)
    if cls is None:
        return None
    kwargs = {k: v for k, v in data.items() if k != "cls"}
    if cls is tl_types.InputPeerEmpty:
        return None
    return cls(**kwargs)


def message_to_dict(message) -> dict[str, Any]:
    """Full message normalizer — the canonical JSON export shape (schema v1)."""
    sender = getattr(message, "sender", None)
    reply_to = getattr(message, "reply_to", None)
    reply_id = None
    if reply_to is not None:
        reply_id = getattr(reply_to, "reply_to_msg_id", None) or getattr(reply_to, "reply_to_msg_id", None)
        if reply_id is None and getattr(reply_to, "reply_to", None) is not None:
            reply_id = getattr(reply_to.reply_to, "message_id", None)

    media = classify_media(message)
    return {
        "id": int(getattr(message, "id", 0)),
        "date": isoformat(getattr(message, "date", None)),
        "edited": isoformat(getattr(message, "edit_date", None)),
        "sender": sender_info(sender),
        "text": getattr(message, "message", "") or "",
        "entities": serialize_entities(message),
        "reply_to": reply_id,
        "forwarded_from": serialize_forward(message),
        "reactions": serialize_reactions(message),
        "views": getattr(message, "views", None),
        "forwards": getattr(message, "forwards", None),
        "media": media,
    }
