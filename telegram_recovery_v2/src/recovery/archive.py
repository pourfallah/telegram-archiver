"""Lossless source archive: read from A, write canonical JSON + raw + media.

Archive layout (per run):

    <run>/archive/
        archive_meta.json          # run metadata, peer info, counts
        messages.ndjson            # one canonical message per line
        raw_messages.ndjson        # sanitized raw MTProto object per line
        media/
            media_index.json       # one record per media item
            files/<media_id>.bin   # original bytes
        reactions.ndjson           # per-message reaction records
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import mimetypes
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from telethon import functions, types
from telethon.tl.types import Message, MessageMediaDocument, MessageMediaPhoto
from telethon.utils import get_peer_id


def utcnow_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def raw_to_json(obj) -> dict:
    """Serialize any MTProto TL object to a JSON-safe dict.

    Removes nothing except the lower-level auth/session material, which never
    appears inside Message objects anyway. Unknown future fields are kept via
    Telethon's serialization of the full object.
    """
    return json.loads(json.dumps(obj.to_dict(), default=str))


def constructor_name(obj) -> str:
    return type(obj).__name__


# ----------------------------------------------------------------- media


@dataclass
class MediaRecord:
    media_id: str
    source_message_id: int
    type: str  # photo / video / gif / audio / voice / sticker / document / contact ...
    constructor: str
    filename: str | None = None
    mime: str | None = None
    size: int | None = None
    sha256: str | None = None
    document_id: int | None = None
    access_hash: int | None = None
    file_reference: str | None = None  # base64
    width: int | None = None
    height: int | None = None
    duration: float | None = None
    attributes: list = field(default_factory=list)
    grouped_id: int | None = None
    local_file: str | None = None  # path within archive media/files
    caption: str | None = None
    caption_entities: list = field(default_factory=list)
    extra: dict = field(default_factory=dict)  # e.g. stickerset, waveform

    def to_json(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}


def classify_media(msg: Message) -> str:
    m = msg.media
    if m is None:
        return "none"
    if isinstance(m, MessageMediaPhoto):
        return "photo"
    if isinstance(m, MessageMediaDocument):
        doc = m.document
        if doc is None:
            return "document"
        attrs = doc.attributes or []
        names = {type(a).__name__ for a in attrs}
        if "DocumentAttributeSticker" in names:
            return "sticker"
        if "DocumentAttributeAnimated" in names:
            return "gif"
        if "DocumentAttributeAudio" in names:
            a = next(a for a in attrs if type(a).__name__ == "DocumentAttributeAudio")
            return "voice" if getattr(a, "voice", False) else "audio"
        if "DocumentAttributeVideo" in names:
            return "video"
        return "document"
    return constructor_name(m).replace("MessageMedia", "").lower()


def document_filename(doc) -> str | None:
    for a in doc.attributes or []:
        if isinstance(a, types.DocumentAttributeFilename):
            return a.file_name
    return None


def serialize_attributes(attrs) -> list[dict]:
    out = []
    for a in attrs or []:
        d = raw_to_json(a)
        out.append({"_": constructor_name(a), **d})
    return out


def build_media_record(msg: Message, grouped_id=None) -> MediaRecord | None:
    if msg.media is None:
        return None
    mtype = classify_media(msg)
    mid = f"{msg.id}_{mtype}"
    rec = MediaRecord(
        media_id=mid,
        source_message_id=msg.id,
        type=mtype,
        constructor=constructor_name(msg.media),
        grouped_id=int(grouped_id) if grouped_id else None,
        caption=msg.message or None,
        caption_entities=[raw_to_json(e) for e in (msg.entities or [])] if msg.message else [],
    )
    if isinstance(msg.media, MessageMediaPhoto):
        photo = msg.media.photo
        if photo is not None:
            rec.document_id = photo.id
            rec.access_hash = photo.access_hash
            rec.file_reference = __import__("base64").b64encode(photo.file_reference).decode()
            rec.extra["date"] = photo.date.isoformat() if photo.date else None
            rec.extra["sizes"] = [raw_to_json(s) for s in (photo.sizes or [])]
            rec.extra["spoiler"] = bool(msg.media.spoiler)
            big = [s for s in (photo.sizes or []) if isinstance(s, types.PhotoSize)]
            if big:
                rec.width, rec.height = big[-1].w, big[-1].h
    elif isinstance(msg.media, MessageMediaDocument):
        doc = msg.media.document
        if doc is not None:
            rec.document_id = doc.id
            rec.access_hash = doc.access_hash
            rec.file_reference = __import__("base64").b64encode(doc.file_reference).decode()
            rec.mime = doc.mime_type
            rec.size = doc.size
            rec.filename = document_filename(doc)
            rec.attributes = serialize_attributes(doc.attributes)
            for a in doc.attributes or []:
                if isinstance(a, types.DocumentAttributeVideo):
                    rec.width, rec.height = a.w, a.h
                    rec.duration = a.duration
                    rec.extra["supports_streaming"] = bool(a.supports_streaming)
                elif isinstance(a, types.DocumentAttributeAudio):
                    rec.duration = a.duration
                    rec.extra["title"] = a.title
                    rec.extra["performer"] = a.performer
                    if a.waveform:
                        rec.extra["waveform_b64"] = __import__("base64").b64encode(a.waveform).decode()
                elif isinstance(a, types.DocumentAttributeSticker):
                    rec.extra["sticker_alt"] = a.alt
                    rec.extra["stickerset"] = raw_to_json(a.stickerset)
            rec.extra["spoiler"] = bool(msg.media.spoiler)
    else:
        rec.extra["raw"] = raw_to_json(msg.media)
    return rec


# ---------------------------------------------------------------- archive


@dataclass
class CanonicalMessage:
    """ONE Telegram Message = ALL of its properties. Never flattened."""

    message_id: int
    peer_id: int | None
    sender_id: int | None
    sender_label: str | None  # "A" / "B" when it maps to our accounts
    date: str
    edit_date: str | None
    text: str | None
    entities: list
    media: MediaRecord | None
    reply_to: dict | None
    grouped_id: int | None
    forward: dict | None
    reactions: list
    views: int | None
    forwards: int | None
    replies_count: int | None
    via_bot: str | None
    silent: bool | None
    post: bool | None
    out: bool | None
    raw: dict  # full sanitized MTProto snapshot

    def to_json(self) -> dict:
        d = self.__dict__.copy()
        d["media"] = self.media.to_json() if self.media else None
        return {k: v for k, v in d.items() if v is not None}


class ArchiveWriter:
    def __init__(self, run_dir: Path) -> None:
        self.dir = run_dir / "archive"
        self.media_dir = self.dir / "media" / "files"
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self._msgs = open(self.dir / "messages.ndjson", "w", encoding="utf-8")
        self._raws = open(self.dir / "raw_messages.ndjson", "w", encoding="utf-8")
        self._reacts = open(self.dir / "reactions.ndjson", "w", encoding="utf-8")
        self._media_records: list[MediaRecord] = []
        self._media_by_id: dict[str, MediaRecord] = {}
        self._media_index: list[dict] = []
        self.count = 0
        self.meta: dict = {}

    def write_message(self, msg: Message, sender_label: str | None) -> CanonicalMessage:
        media = build_media_record(msg, msg.grouped_id)
        fwd = None
        if msg.fwd_from is not None:
            f = msg.fwd_from
            fwd = {
                "date": f.date.isoformat() if f.date else None,
                "from_id": raw_to_json(f.from_id) if f.from_id else None,
                "from_name": f.from_name,
                "imported": bool(f.imported),
                "channel_post": f.channel_post,
                "post_author": f.post_author,
                "saved_from_peer": raw_to_json(f.saved_from_peer) if f.saved_from_peer else None,
                "saved_from_msg_id": f.saved_from_msg_id,
            }
        reply_to = None
        if msg.reply_to is not None:
            r = msg.reply_to
            reply_to = {
                "reply_to_msg_id": getattr(r, "reply_to_msg_id", None),
                "top_msg_id": getattr(r, "reply_to_top_id", None),
                "quote_text": getattr(r, "quote_text", None),
                "quote_entities": [raw_to_json(e) for e in (getattr(r, "quote_entities", None) or [])],
            }
        reactions = []
        if msg.reactions is not None:
            for rc in msg.reactions.results or []:
                reactions.append(
                    {
                        "reaction": raw_to_json(rc.reaction),
                        "count": rc.count,
                        "reaction_order": getattr(rc, "reaction_order", None),
                    }
                )
        raw = raw_to_json(msg)
        cm = CanonicalMessage(
            message_id=msg.id,
            peer_id=get_peer_id(msg.peer_id) if msg.peer_id else None,
            sender_id=get_peer_id(msg.from_id) if msg.from_id else None,
            sender_label=sender_label,
            date=msg.date.isoformat() if msg.date else None,
            edit_date=msg.edit_date.isoformat() if msg.edit_date else None,
            text=msg.message,
            entities=[raw_to_json(e) for e in (msg.entities or [])],
            media=media,
            reply_to=reply_to,
            grouped_id=int(msg.grouped_id) if msg.grouped_id else None,
            forward=fwd,
            reactions=reactions,
            views=msg.views,
            forwards=msg.forwards,
            replies_count=getattr(msg.replies, "replies", None) if msg.replies else None,
            via_bot=str(msg.via_bot_id) if getattr(msg, "via_bot_id", None) else None,
            silent=msg.silent,
            post=bool(msg.post),
            out=bool(msg.out),
            raw=raw,
        )
        self._msgs.write(json.dumps(cm.to_json(), ensure_ascii=False) + "\n")
        self._raws.write(json.dumps(raw, ensure_ascii=False) + "\n")
        for r in reactions:
            self._reacts.write(
                json.dumps({"source_message_id": msg.id, **r}, ensure_ascii=False) + "\n"
            )
        if media is not None:
            self._media_records.append(media)
            self._media_by_id[media.media_id] = media
            self._media_index.append(media.to_json())
        self.count += 1
        return cm

    async def download_media(self, client, msg: Message, media: MediaRecord) -> None:
        """Download original bytes; record sha256 and local path."""
        if media is None or media.type == "none":
            return
        try:
            path = await client.download_media(msg, file=str(self.media_dir / f"{media.media_id}"))
        except Exception:
            path = None
        if path is None:
            media.extra["download_error"] = "download failed"
            return
        p = Path(path)
        media.local_file = f"media/files/{p.name}"
        media.sha256 = sha256_file(p)
        if not media.mime:
            media.mime = mimetypes.guess_type(p.name)[0]

    def finalize(self) -> dict:
        self._msgs.close()
        self._raws.close()
        self._reacts.close()
        # Re-read messages to pick up local_file/sha256 set during download,
        # rewrite messages.ndjson and the media index with the final records.
        final: list[dict] = []
        with open(self.dir / "messages.ndjson", encoding="utf-8") as f:
            for line in f:
                final.append(json.loads(line))
        for m in final:
            if m.get("media"):
                rec = self._media_by_id.get(m["media"]["media_id"])
                if rec is not None:
                    m["media"] = rec.to_json()
        with open(self.dir / "messages.ndjson", "w", encoding="utf-8") as f:
            for m in final:
                f.write(json.dumps(m, ensure_ascii=False) + "\n")
        self._media_index = [r.to_json() for r in self._media_records]
        with open(self.dir / "media" / "media_index.json", "w", encoding="utf-8") as f:
            json.dump(self._media_index, f, ensure_ascii=False, indent=2)
        self.meta = {
            "message_count": len(final),
            "media_count": len(self._media_index),
            "finished_at": utcnow_iso(),
        }
        with open(self.dir / "archive_meta.json", "w", encoding="utf-8") as f:
            json.dump(self.meta, f, indent=2)
        return self.meta


class ArchiveReader:
    """Stream a written archive back (for package building / verification)."""

    def __init__(self, run_dir: Path) -> None:
        self.dir = run_dir / "archive"

    def messages(self):
        with open(self.dir / "messages.ndjson", encoding="utf-8") as f:
            for line in f:
                yield json.loads(line)

    def media_index(self) -> list[dict]:
        return json.loads((self.dir / "media" / "media_index.json").read_text())

    def meta(self) -> dict:
        return json.loads((self.dir / "archive_meta.json").read_text())
