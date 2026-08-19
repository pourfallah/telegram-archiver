"""Simple media downloader.

For each (message, media-descriptor) pair in an export batch it downloads the
media payload to ``out_dir/media/<type>/<filename>``, streams it through
SHA-256, then updates the ``MediaFile`` ledger row (path + hash + status).
Downloads are concurrency-capped and retried a bounded number of times; a
permanent failure is recorded so the retry endpoint can re-queue it.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.models import ChatExport, MediaFile

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3


def _pair(messages: list, rows: list[dict]) -> list[tuple[Any, dict]]:
    """Zip raw messages with their classified media descriptors (order kept)."""
    out: list[tuple[Any, dict]] = []
    for msg, row in zip(messages, rows, strict=False):
        for media in row.get("media") or []:
            out.append((msg, media))
    return out


class MediaDownloader:
    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        concurrency: int | None = None,
        log=logger,
    ) -> None:
        self._settings = settings
        self._sf = session_factory
        self._concurrency = concurrency or settings.media_concurrency
        self._log = log

    async def download_batch(
        self,
        client,
        export: ChatExport,
        out_dir: Path,
        messages: list,
        rows: list[dict],
    ) -> None:
        sem: asyncio.Semaphore = asyncio.Semaphore(self._concurrency)

        async def _one(msg: Any, media: dict) -> None:
            async with sem:
                await self._download_one(client, export, out_dir, msg, media)

        await asyncio.gather(
            *(_one(m, d) for m, d in _pair(messages, rows)),
            return_exceptions=True,
        )

    async def _download_one(
        self, client, export: ChatExport, out_dir: Path, msg: Any, media: dict
    ) -> None:
        media_type = (media.get("type") or "document").split("/")[0]
        subdir = out_dir / "media" / media_type
        subdir.mkdir(parents=True, exist_ok=True)
        filename = media.get("filename") or "file.bin"
        dest = subdir / filename

        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                await client.download_media(msg, file=str(dest))
                sha = hashlib.sha256(dest.read_bytes()).hexdigest()
            except Exception as exc:  # noqa: BLE001
                if attempt >= MAX_ATTEMPTS:
                    await self._set_status(
                        export, msg, media_type, "failed",
                        error=f"{type(exc).__name__}: {exc}",
                    )
                    self._log.warning(
                        "Media download failed for msg %s: %s", getattr(msg, "id", "?"), exc
                    )
                    return
                await asyncio.sleep(2 ** attempt)
                continue

            rel = f"media/{media_type}/{filename}"
            await self._set_status(export, msg, media_type, "downloaded", sha=sha, path=rel)
            return

    async def _set_status(
        self,
        export: ChatExport,
        msg: Any,
        media_type: str,
        status: str,
        sha: str | None = None,
        path: str | None = None,
        error: str | None = None,
    ) -> None:
        """Record the download result on the matching MediaFile ledger row."""
        async with self._sf() as db:
            row = await db.scalar(
                select(MediaFile).where(
                    MediaFile.chat_export_id == export.id,
                    MediaFile.message_id == getattr(msg, "id", 0),
                    MediaFile.media_type == media_type,
                )
            )
            if row is None:
                return
            row.status = status
            row.sha256 = sha
            if path is not None:
                row.file_path = path
            if error is not None:
                row.error = error
                row.attempts = MAX_ATTEMPTS
            else:
                row.attempts = 0
            if status == "downloaded":
                export.files_downloaded = (export.files_downloaded or 0) + 1
                await db.execute(
                    ChatExport.__table__.update()
                    .where(ChatExport.id == export.id)
                    .values(files_downloaded=export.files_downloaded)
                )
            await db.commit()
