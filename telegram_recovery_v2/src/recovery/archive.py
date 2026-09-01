"""Lossless source archive for Telegram Recovery v2.

Canonical representation is ONE message = ONE record, preserving the complete
set of properties: text, entities, media, caption, reply, forward, grouped
album id, reactions, flags, and a sanitized raw MTProto snapshot.

Layout of an archive directory::

    <run>/archive/
        manifest.json        counts, ranges, runner info, run_id
        messages.ndjson      canonical records (one JSON object per line)
        raw/
          raw.ndjson         sanitized raw MTProto snapshot (same line order)
        media/               downloaded source media (when enabled)
        reactions/
          archive.json       detailed reaction (reactor, reaction, message)
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from .media import classify_media
from .telegram_client import tl_to_plain

SCHEMA_VERSION = 1

ENTITY_EXTRAS = ("url", "user_id", "language", "document_id", "sentiment")


def _ismap(__tl__: str) -> str:
    return __tl__.replace("MessageEntity", "").lower() if __tl__ else "unknown"


def _entities(message) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for e in getattr(message, "entities", None) or []:
        rec = {
            "type": _ismap(type(e).__name__),
            "offset": getattr(e, "offset", 0),
            "length": getattr(e, "length", 0),
            "__tl__": type(e).__name__,
        }
        for k in ENTITY_EXTRAS:
            v = getattr(e, k, None)
            if v is not None:
                rec[k] = tl_to_plain(v)
        out.append(rec)
    return out


def _reply_to(message) -> dict[str, Any] | None:
    r = getattr(message, "reply_to", None)
    if r is None:
        return None
    return {
        "reply_to_msg_id": getattr(r, "reply_to_msg_id", None),
        "reply_to_top_id": getattr(r, "reply_to_top_id", None),
        "reply_to_peer_id": tl_to_plain(getattr(r, "reply_to_peer_id", None)),
        "quote": bool(getattr(r, "quote", False)),
        "quote_text": getattr(r, "quote_text", None),
        "quote_entities": [tl_to_plain(e) for e in (getattr(r, "quote_entities", None) or [])],
        "quote_offset": getattr(r, "quote_offset", None),
        "__tl__": "MessageReplyHeader",
    }


def _forward(message) -> dict[str, Any] | None:
    f = getattr(message, "fwd_from", None)
    if f is None:
        return None
    return {
        "__tl__": "MessageFwdHeader",
        "date": tl_to_plain(getattr(f, "date", None)),
        "imported": bool(getattr(f, "imported", False)),
        "from_id": tl_to_plain(getattr(f, "from_id", None)),
        "from_name": getattr(f, "from_name", None),
        "channel_post": getattr(f, "channel_post", None),
        "post_author": getattr(f, "post_author", None),
        "saved_from_peer": tl_to_plain(getattr(f, "saved_from_peer", None)),
        "saved_from_msg_id": getattr(f, "saved_from_msg_id", None),
    }


def _flags(message) -> dict[str, bool | None]:
    return {k: bool(getattr(message, k, False)) for k in (
        "out", "mentioned", "media_unread", "silent", "post", "from_scheduled",
        "legacy", "edit_hide", "pinned", "noforwards", "invert_media",
        "video_processing_pending", "from_boosts_applied",
    )}


def _reaction_summary(message) -> dict[str, Any] | None:
    reactions = getattr(message, "reactions", None)
    results = getattr(reactions, "results", None)
    if not results:
        return None
    rows = []
    for r in results:
        reaction = getattr(r, "reaction", None)
        rows.append({
            "reaction": tl_to_plain(reaction),
            "count": getattr(r, "count", 0),
            "chosen": bool(getattr(r, "chosen", False)),
        })
    return {"rows": rows,
            "reactions_are_possible": bool(getattr(message, "reactions_are_possible", True))}


def build_canonical_record(message) -> dict[str, Any]:
    """One lossless structured record for one source Telegram message."""
    media = classify_media(message)
    text = getattr(message, "message", "") or ""
    from_id = tl_to_plain(getattr(message, "from_id", None))
    return {
        "schema_version": SCHEMA_VERSION,
        "source_message_id": int(getattr(message, "id", 0)),
        "peer_id": tl_to_plain(getattr(message, "peer_id", None)),
        "date": tl_to_plain(getattr(message, "date", None)),
        "edit_date": tl_to_plain(getattr(message, "edit_date", None)),
        "from_id": from_id,
        "text": text,
        # A media message's text IS its caption — the same source Message.
        "caption": text if media else None,
        "entities": _entities(message),
        "caption_entities": _entities(message) if media else None,
        "media": media,
        "reply_to": _reply_to(message),
        "forward": _forward(message),
        "grouped_id": getattr(message, "grouped_id", None),
        "reactions": _reaction_summary(message),
        "views": getattr(message, "views", None),
        "forwards_count": getattr(message, "forwards", None),
        "flags": _flags(message),
    }


# ------------------------------------------------------------------------
# Writers / readers
# ------------------------------------------------------------------------
class Archive:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.messages_path = self.root / "messages.ndjson"
        self.raw_dir = self.root / "raw"
        self.raw_path = self.raw_dir / "raw.ndjson"
        self.media_dir = self.root / "media"
        self.reactions_dir = self.root / "reactions"
        self.manifest_path = self.root / "manifest.json"

    def create(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self.reactions_dir.mkdir(parents=True, exist_ok=True)

    def write_manifest(self, manifest: dict[str, Any]) -> None:
        manifest.setdefault("schema_version", SCHEMA_VERSION)
        self.manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    def append_canonical(self, record: dict[str, Any]) -> None:
        with self.messages_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    def append_raw(self, raw: dict[str, Any]) -> None:
        with self.raw_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(raw, ensure_ascii=False) + "\n")

    def is_resumable(self) -> bool:
        return self.messages_path.exists() and self.raw_path.exists()

    # ------------------------------------------------------------------
    def read_messages(self) -> Iterator[dict[str, Any]]:
        if not self.messages_path.exists():
            return
        with self.messages_path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield json.loads(line)

    def read_raw(self) -> Iterator[dict[str, Any]]:
        if not self.raw_path.exists():
            return
        with self.raw_path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield json.loads(line)

    def messages_count(self) -> int:
        return sum(1 for _ in self.read_messages())

    def writer_stats(self) -> dict[str, Any]:
        return {
            "messages": self.messages_count(),
            "media_on_disk": sum(1 for _ in self.media_dir.rglob("*") if _.is_file()),
        }