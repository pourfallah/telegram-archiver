"""Media handling for Telegram Recovery v2.

Every source media becomes a first-class archive object. Classification is
driven by the actual Telethon constructor and document attributes (never by
file extension alone). Original bytes are preserved, streamed to disk with a
SHA-256, with an optional resumable checkpoint.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

from .telegram_client import tl_to_plain

_SAFE = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._- "


def safe_filename(name: str, fallback: str = "file") -> str:
    cleaned = "".join(c if c in _SAFE else "_" for c in name).strip(" .")
    if not cleaned or cleaned in (".", "..") or len(cleaned) > 180:
        return fallback
    return cleaned


def _attr_dict(attr: Any) -> dict:
    """Describe a single DocumentAttribute."""
    return {"__tl__": type(attr).__name__, **tl_to_plain(attr)}


def classify_media(message: Any) -> list[dict[str, Any]]:
    """Describe every media payload in one source message.

    Returns one dict per media (a Message carries at most one ``media``
    object, but albums are separate Messages sharing a ``grouped_id``).
    """
    media = getattr(message, "media", None)
    if media is None:
        return []
    out: list[dict[str, Any]] = []
    kind = type(media).__name__

    if kind == "MessageMediaPhoto":
        photo = getattr(media, "photo", None)
        sizes = []
        for s in getattr(photo, "sizes", None) or []:
            sizes.append({
                "type": getattr(s, "type", None),
                "width": getattr(s, "w", None),
                "height": getattr(s, "h", None),
                "size_bytes": getattr(s, "size", None),
            })
        out.append({
            "media_id": getattr(photo, "id", None),
            "source_message_id": getattr(message, "id", None),
            "type": "photo",
            "constructor": "MessageMediaPhoto",
            "access_hash": getattr(photo, "access_hash", None),
            "file_reference": tl_to_plain(getattr(photo, "file_reference", None)),
            "sizes": sizes,
            "date": tl_to_plain(getattr(photo, "date", None)),
            "spoiler": bool(getattr(media, "spoiler", False)),
            "mime": "application/octet-stream",   # photos resolve to jpeg/png on download
            "width": None,
            "height": None,
            "duration": None,
            "filename": None,
            "attributes": [],
            "thumbnail": None,
        })

    elif kind == "MessageMediaDocument":
        doc = getattr(media, "document", None)
        attributes = [_attr_dict(a) for a in getattr(doc, "attributes", None) or []]
        mtype = _document_type(attributes)
        out.append({
            "media_id": getattr(doc, "id", None),
            "source_message_id": getattr(message, "id", None),
            "type": mtype,
            "constructor": "MessageMediaDocument",
            "access_hash": getattr(doc, "access_hash", None),
            "file_reference": tl_to_plain(getattr(doc, "file_reference", None)),
            "mime": getattr(doc, "mime_type", None) or "application/octet-stream",
            "size_bytes": getattr(doc, "size", None),
            "filename": safe_filename(getattr(doc, "file_name", None) or f"{mtype}",
                                      fallback=mtype),
            "spoiler": bool(getattr(media, "spoiler", False)),
            "round": bool(getattr(media, "round", False)),
            "voice": bool(getattr(media, "voice", False)),
            "attributes": attributes,
            "width": None,
            "height": None,
            "duration": None,
            "thumbnail": None,
        })
        # enrich common scoped attributes
        for a in getattr(doc, "attributes", None) or []:
            _enrich(a, out[-1])

    elif kind == "MessageMediaWebPage":
        out.append({
            "type": "webpage", "constructor": "MessageMediaWebPage",
            "source_message_id": getattr(message, "id", None),
            "url": getattr(getattr(media, "webpage", None), "url", None),
        })
    else:
        # Unrecognized media (geo, contact, poll, game, invoice, etc.) —
        # captured structurally so nothing is silently dropped.
        out.append({
            "type": "other", "constructor": kind,
            "source_message_id": getattr(message, "id", None),
            "detail": tl_to_plain(media),
        })
    return out


def _enrich(attr: Any, media: dict) -> None:
    n = type(attr).__name__
    if n == "DocumentAttributeVideo":
        media.update(width=attr.w, height=attr.h, duration=attr.duration,
                     round=bool(getattr(attr, "round_message", False)),
                     supports_streaming=bool(getattr(attr, "supports_streaming", False)))
    elif n == "DocumentAttributeAudio":
        media.update(duration=attr.duration,
                     title=getattr(attr, "title", None),
                     performer=getattr(attr, "performer", None),
                     voice=bool(getattr(attr, "voice", False)),
                     waveform=tl_to_plain(getattr(attr, "waveform", None)))
    elif n == "DocumentAttributeSticker":
        media["sticker"] = {
            "alt": getattr(attr, "alt", None),
            "stickerset": tl_to_plain(getattr(attr, "stickerset", None)),
            "mask": bool(getattr(attr, "mask", False)),
            "mask_coords": tl_to_plain(getattr(attr, "mask_coords", None)),
        }
    elif n == "DocumentAttributeFilename":
        media["filename"] = safe_filename(attr.file_name, fallback="document")


def _document_type(attributes: list[dict]) -> str:
    for a in attributes:
        t = a["__tl__"]
        if t == "DocumentAttributeSticker":
            return "sticker"
        if t == "DocumentAttributeVideo":
            return "video"
        if t == "DocumentAttributeAudio":
            return "voice" if a.get("voice") else "audio"
        if t == "DocumentAttributeAnimated":
            return "animation"
    return "document"


# ------------------------------------------------------------------------
# Downloading (streaming, hashing, resumable)
# ------------------------------------------------------------------------
def _local_name(media_rec: dict, idx: int) -> str:
    base = media_rec.get("filename") or media_rec.get("type") or "media"
    ext = Path(base).suffix
    stem = Path(base).stem
    return f"{media_rec['source_message_id']}__{idx}__{safe_filename(stem)}"
    # caller appends extension via ext when writing


class MediaDownloader:
    """Download source media payloads to an archive media/ tree.

    - stream iter_download -> SHA-256 while writing
    - optional checkpoint file (sha256 + size) so an interrupted run resumes
      without re-downloading files whose size+hash already match
    """

    def __init__(self, client, dest_dir: Path, resume: bool = True,
                 concurrency: int = 2) -> None:
        self.client = client
        self.dest = Path(dest_dir)
        self.dest.mkdir(parents=True, exist_ok=True)
        self.resume = resume
        self.concurrency = max(1, concurrency)
        self._sem = asyncio.Semaphore(self.concurrency)

    async def download_all(self, message, media_recs: list[dict]) -> list[dict]:
        """Download every media in ``media_recs`` for one message.

        Returns the list updated with local relative path + sha256 (+size).
        """
        results: list[dict] = []
        async def _one(idx, rec):
            async with self._sem:
                rec2 = dict(rec)
                await self._download_one(message, rec2, idx)
                results.append(rec2)
        await asyncio.gather(*(_one(i, r) for i, r in enumerate(media_recs)))
        return results

    async def _download_one(self, message, rec: dict, idx: int) -> None:
        try:
            media = getattr(message, "media", None)
            if media is None:
                rec["error"] = "no media payload on message"
                return
            img = getattr(media, "photo", None) or getattr(media, "document", None)
            if img is None:
                rec["error"] = "no downloadable object (web page / geo / contact)"
                return
            if getattr(media, "webpage", ...) is not None and type(media).__name__ == "MessageMediaWebPage":
                rec["error"] = "webpage preview has no file"
                return

            ext = Path(rec.get("filename") or rec["type"]).suffix or ".bin"
            name = _local_name(rec, idx) + ext
            dest = self.dest / name

            # Resume: keep file if bytes already match the checkpoint.
            # Telethon ImageLocation.byte_length gives the true size when known.
            expected_size = getattr(img, "size", None)

            if self.resume and dest.exists() and self._matches_checkpoint(rec, expected_size):
                rec["path"] = str(dest.relative_to(self.dest.parent))
                rec["sha256"] = self._hash_of(dest)
                rec["size_bytes"] = dest.stat().st_size
                return

            tmp = dest.with_suffix(ext + ".part")
            hasher = hashlib.sha256()
            size = 0
            async for chunk in self.client.iter_download(media):
                hasher.update(chunk)
                size += len(chunk)
                tmp.open("ab").write(chunk)
            tmp.replace(dest)

            sha = hasher.hexdigest()
            rec["sha256"] = sha
            rec["size_bytes"] = size
            rec["path"] = str(dest.relative_to(self.dest.parent))
            self._write_checkpoint(rec, expected_size, sha, size)
        except Exception as exc:  # noqa: BLE001
            rec["error"] = f"{type(exc).__name__}: {exc}"

    def _checkpoint(self, rec: dict) -> Path:
        mid = rec["source_message_id"]
        return self.dest / f".sha256_{mid}.json"

    def _matches_checkpoint(self, rec: dict, expected_size) -> bool:
        c = self._checkpoint(rec)
        if not c.exists():
            return False
        try:
            data = json.loads(c.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return False
        return (data.get("sha256") == rec.get("sha256")) and (
            expected_size is None or data.get("size") == expected_size
        )

    def _write_checkpoint(self, rec: dict, expected_size, sha: str, size: int) -> None:
        if not self.resume:
            return
        self._checkpoint(rec).write_text(
            json.dumps({"sha256": sha, "size": size, "expected": expected_size}),
            encoding="utf-8",
        )

    def _hash_of(self, path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                h.update(block)
        return h.hexdigest()