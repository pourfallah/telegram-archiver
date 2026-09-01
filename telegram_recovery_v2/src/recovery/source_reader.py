"""Source reader: page a Telegram chat and stream it into the lossless archive.

Iterates newest -> oldest via ``messages.getHistory`` (explicit pagination), is
paced to avoid FloodWait, streams canonical + raw records to disk (never loads
the whole history into RAM), and optionally downloads media inline.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator

from telethon.tl.functions import messages as tg_messages

from .archive import Archive, build_canonical_record
from .media import MediaDownloader
from .telegram_client import RecoveryClient, tl_to_plain

logger = logging.getLogger("recovery.reader")

BATCH = 500


class SourceReader:
    def __init__(self, client: RecoveryClient, peer, archive: Archive,
                 downloader: MediaDownloader | None = None,
                 msgs_per_sec: float = 2.0, burst: int = 5) -> None:
        self.client = client
        self.peer = peer
        self.archive = archive
        self.downloader = downloader
        self.msgs_per_sec = max(0.0, msgs_per_sec)
        self._interval = 1.0 / self.msgs_per_sec if self.msgs_per_sec else 0.0
        self.burst = max(1, burst)
        self._burst_remaining = 0

    async def _pace(self) -> None:
        if self._interval <= 0:
            return
        if self._burst_remaining > 0:
            self._burst_remaining -= 1
            return
        await asyncio.sleep(self._interval)
        self._burst_remaining = self.burst

    async def stream_all(self, max_messages: int | None = None,
                         resume_after: int | None = None) -> dict[str, Any]:
        """Stream the whole chat into the archive. Returns counters.

        ``resume_after``: highest source message already written on a previous
        run. Newer-than-offset helpers are not available on getHistory, so an
        interrupted run resumes by re-sending batches but skipping already
        written ids from the archive. Deterministic and never duplicates even
        on a crash — ids already on disk are overwritten rather than doubled.
        """
        existing = {r["source_message_id"] for r in self.archive.read_messages()} \
            if resume_after is not None else set()
        offset_id = 0
        seen = 0
        media_stats = {"downloaded": 0, "failed": 0}
        while True:
            await self._pace()
            result = await self.client.call(
                tg_messages.GetHistoryRequest(self.peer, offset_id=offset_id,
                                              offset_date=None, add_offset=0,
                                              limit=BATCH, max_id=0, min_id=0, hash=0))
            messages = getattr(result, "messages", None) or []
            if not messages:
                break
            for m in messages:
                if max_messages is not None and seen >= max_messages:
                    return self._counters(seen, media_stats)
                mid = int(getattr(m, "id", 0))
                if mid in existing:
                    continue
                record = build_canonical_record(m)
                if self.downloader is not None and record.get("media"):
                    downloaded = await self.downloader.download_all(m, record["media"])
                    record["media"] = downloaded
                    for d in downloaded:
                        if d.get("error"):
                            media_stats["failed"] += 1
                        elif d.get("path"):
                            media_stats["downloaded"] += 1
                self.archive.append_canonical(record)
                self.archive.append_raw(tl_to_plain(m))
                seen += 1
            # oldest in this batch
            offset_id = messages[-1].id
            if len(messages) < BATCH:
                break
        return self._counters(seen, media_stats)

    def _counters(self, seen: int, media: dict) -> dict[str, Any]:
        return {"messages": seen, "media_downloaded": media["downloaded"],
                "media_failed": media["failed"]}