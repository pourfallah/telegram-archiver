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
    "MessageEntitySpoiler": "spoiler",
    "MessageEntityCustomEmoji": "custom_emoji",
    "MessageEntityUnknown": "unknown",
    "MessageEntityBankCard": "bank_card",
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


def serialize_reply(message) -> dict[str, Any] | None:
    """Serialize the reply header including quote when available."""
    reply_to = getattr(message, "reply_to", None)
    if reply_to is None:
        return None
    out: dict[str, Any] = {}
    reply_msg_id = getattr(reply_to, "reply_to_msg_id", None)
    nested = getattr(reply_to, "reply_to", None)
    if reply_msg_id is None and nested is not None:
        reply_msg_id = getattr(nested, "message_id", None)
    out["reply_to_msg_id"] = reply_msg_id
    out["reply_to_peer_id"] = getattr(reply_to, "reply_to_peer_id", None)
    out["top_msg_id"] = getattr(reply_to, "reply_to_top_id", None)
    out["quote"] = getattr(reply_to, "quote", None)
    if getattr(reply_to, "quote_entities", None):
        out["quote_entities"] = _entities_to_dicts(reply_to.quote_entities)
    return out


def _entities_to_dicts(entities) -> list[dict[str, Any]]:
    out = []
    for entity in entities or []:
        kind = ENTITY_TYPE_MAP.get(type(entity).__name__, type(entity).__name__)
        item: dict[str, Any] = {
            "type": kind,
            "offset": getattr(entity, "offset", 0),
            "length": getattr(entity, "length", 0),
        }
        for attr in ("url", "language", "user_id"):
            if hasattr(entity, attr) and getattr(entity, attr) is not None:
                item[attr] = getattr(entity, attr)
        doc_id = getattr(entity, "document_id", None)
        if doc_id is not None:
            item["document_id"] = doc_id
        out.append(item)
    return out


def serialize_entities(message) -> list[dict[str, Any]]:
    """Flatten Telegram entity objects into plain dicts.

    Offsets/lengths are kept exactly as Telegram reports them (UTF-16 code
    units). For custom emoji the document_id is preserved — a custom emoji is
    NEVER downgraded to a plain string. The fallback emoji lives in the text.
    """
    return _entities_to_dicts(getattr(message, "entities", None) or [])


def serialize_forward(message) -> dict[str, Any] | None:
    fwd = getattr(message, "forward", None)
    if fwd is None:
        return None
    name = None
    origin = getattr(fwd, "from_id", None)
    origin_id = None
    if origin is not None:
        origin_id = (getattr(origin, "user_id", None)
                     or getattr(origin, "channel_id", None)
                     or getattr(origin, "chat_id", None))
    if getattr(fwd, "chat", None) is not None:
        name = fwd.chat.title or fwd.chat.first_name
    elif getattr(fwd, "sender", None) is not None:
        name = fwd.sender.first_name or fwd.sender.username
    out: dict[str, Any] = {
        "from_id": origin_id,
        "name": name,
        "date": isoformat(getattr(fwd, "date", None)),
        "from_name": getattr(fwd, "from_name", None),
        "channel_post": getattr(fwd, "channel_post", None),
        "post_author": getattr(fwd, "post_author", None),
    }
    saved_from = getattr(fwd, "saved_from_peer", None)
    if saved_from is not None:
        out["saved_from_peer_id"] = (getattr(saved_from, "user_id", None)
                                     or getattr(saved_from, "channel_id", None)
                                     or getattr(saved_from, "chat_id", None))
    out["saved_from_msg_id"] = getattr(fwd, "saved_from_msg_id", None)
    return out


def serialize_reactions(message) -> dict[str, Any] | None:
    """Serialize message reactions WITHOUT flattening them.

    Preserves each reaction's type (emoji / custom emoji / paid), count,
    chosen state, and document_id for custom-emoji reactions. Reaction voters
    are fetched separately (messages.getMessageReactionsList).
    """
    reactions = getattr(message, "reactions", None)
    results = getattr(reactions, "results", None)
    if not results:
        return None
    items = []
    for r in results:
        reaction = getattr(r, "reaction", None)
        if reaction is None:
            continue
        rtype = type(reaction).__name__
        emoji = getattr(reaction, "emoticon", None)
        doc_id = getattr(reaction, "document_id", None)
        item: dict[str, Any] = {
            "reaction_type": rtype,
            "count": getattr(r, "count", 0),
        }
        if emoji is not None:
            item["emoji"] = emoji
        if doc_id is not None:
            item["document_id"] = doc_id
        # Chosen reactions (the account's own selection) — flags.chosen
        if getattr(r, "chosen", False):
            item["chosen"] = True
        items.append(item)
    return {"reactions": items} if items else None


async def enrich_reaction_users(
    client,
    peer,
    messages: list,
    voter_limit: int = 20,
    max_messages_with_voters: int = 20,
) -> dict[int, list[dict[str, Any]]]:
    """Fetch reaction voter lists for messages that carry reactions.

    Uses messages.getMessagesReactions (bulk, per batch) and
    messages.getMessageReactionsList (per message, capped) — best effort, never
    raises: if Telegram does not allow it, the archive simply keeps totals.
    Returns {message_id: [ {reaction, peer_id, ...} ]}.
    """
    out: dict[int, list[dict[str, Any]]] = {}
    reacted = [m for m in messages if getattr(getattr(m, "reactions", None), "results", None)]
    if not reacted:
        return out
    try:
        from telethon import functions

        # Bulk reaction state
        try:
            bulk = await client(functions.messages.GetMessagesReactionsRequest(
                peer=peer, id=[m.id for m in reacted]))
            _ = getattr(bulk, "updates", None)  # per-message lists are the source
        except Exception:  # noqa: BLE001 — best effort
            pass

        # Per-message voter lists (capped for flood-safety)
        for m in reacted[:max_messages_with_voters]:
            try:
                rl = await client(functions.messages.GetMessageReactionsListRequest(
                    peer=peer, id=m.id, limit=voter_limit))
                reactions_objs = getattr(rl, "reactions", None) or []
                voters: list[dict[str, Any]] = []
                for item in reactions_objs:
                    r = getattr(item, "reaction", None)
                    peer_obj = getattr(item, "peer_id", None)
                    voters.append({
                        "reaction_type": type(r).__name__ if r else None,
                        "emoji": getattr(r, "emoticon", None) if r else None,
                        "document_id": getattr(r, "document_id", None) if r else None,
                        "peer_id": getattr(peer_obj, "user_id", None)
                        or getattr(peer_obj, "channel_id", None)
                        or getattr(peer_obj, "chat_id", None)
                        if peer_obj else None,
                    })
                if voters:
                    out[int(m.id)] = voters
            except Exception:  # noqa: BLE001 — best effort (e.g. not allowed)
                continue
    except Exception:  # noqa: BLE001
        return out
    return out


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
        meta: dict[str, Any] = {}
        for attr in attrs:
            attr_kind = MEDIA_ATTRIBUTE_MAP.get(type(attr).__name__)
            if attr_kind in {"video", "animation", "sticker"}:
                media_type = attr_kind
            elif attr_kind == "audio":
                media_type = "voice" if getattr(attr, "voice", False) else "audio"
            if hasattr(attr, "file_name") and attr.file_name:
                original = attr.file_name
            # best-effort sub-metadata so nothing is dropped at export time
            if attr_kind == "video":
                meta.update({"duration": getattr(attr, "duration", None),
                             "width": getattr(attr, "w", None), "height": getattr(attr, "h", None),
                             "round": bool(getattr(attr, "round_message", False))})
            elif attr_kind == "audio":
                meta.update({"duration": getattr(attr, "duration", None),
                             "voice": bool(getattr(attr, "voice", False)),
                             "title": getattr(attr, "title", None),
                             "performer": getattr(attr, "performer", None)})
            elif attr_kind == "sticker":
                meta.update({"sticker_emoji": getattr(attr, "emoji", None),
                             "animated": bool(getattr(attr, "animated", False))})
            elif attr_kind == "animation":
                meta["gif"] = True

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
                extra=meta or None,
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


def to_raw_json(message) -> dict[str, Any] | None:
    """Best-effort serialized snapshot of the raw Telegram message object.

    Preserves constructors/ids/access_hashes for future migration, but strips
    secret-ish file references (salt/tokens) and oversized bytes. Used as an
    archival-only fallback so no readable information is dropped at export time.
    """
    try:
        import json as _json

        raw = message.to_dict()
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(raw, dict):
        raw = {"value": raw}

    def _scrub(obj):
        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                if k in ("file_reference", "key", "iv", "data") and isinstance(v, bytes):
                    out[k] = None  # secret-ish bytes → drop value, keep key
                elif k == "document_id" or k == "id":
                    out[k] = _jsafe(v)
                elif isinstance(v, bytes):
                    out[k] = "<bytes>"
                else:
                    out[k] = _scrub(v)
            return out
        if isinstance(obj, list):
            return [_scrub(i) for i in obj]
        if isinstance(obj, bytes):
            return "<bytes>"
        return _jsafe(obj)

    def _jsafe(v):
        if v is None or isinstance(v, (str, int, float, bool)):
            return v
        try:
            _json.dumps(v)
            return v
        except Exception:  # noqa: BLE001
            return str(v)

    return _scrub(raw)


def message_to_dict(message) -> dict[str, Any]:
    """Full message normalizer — the canonical JSON export shape (schema v2)."""
    sender = getattr(message, "sender", None)

    media = classify_media(message)
    return {
        "id": int(getattr(message, "id", 0)),
        "grouped_id": getattr(message, "grouped_id", None),
        "date": isoformat(getattr(message, "date", None)),
        "edited": isoformat(getattr(message, "edit_date", None)),
        "sender": sender_info(sender),
        "text": getattr(message, "message", "") or "",
        "entities": serialize_entities(message),
        "reply_to": serialize_reply(message),
        "forwarded_from": serialize_forward(message),
        "reactions": serialize_reactions(message),
        "views": getattr(message, "views", None),
        "forwards": getattr(message, "forwards", None),
        "replies_count": getattr(getattr(message, "replies", None), "replies", None),
        "via_bot": getattr(message, "via_bot_id", None),
        "post_author": getattr(message, "post_author", None),
        "pinned": bool(getattr(message, "pinned", False)),
        "noforwards": bool(getattr(message, "noforwards", False)),
        "silent": bool(getattr(message, "silent", False)),
        "mentioned": bool(getattr(message, "mentioned", False)),
        "media_unread": bool(getattr(message, "media_unread", False)),
        "post": bool(getattr(message, "post", False)),
        "media": media,
        # True only when Telegram attached a REAL media object (MessageMediaPhoto/
        # MessageMediaDocument/...). Literal "<attached: ...>" text does NOT count.
        "has_media_object": getattr(message, "media", None) is not None,
        # Archival raw snapshot (strip secrets); for future migration only.
        "raw_message": to_raw_json(message),
    }
