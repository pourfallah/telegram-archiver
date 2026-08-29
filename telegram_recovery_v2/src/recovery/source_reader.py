"""Source reader: iterates A-side history for a peer and produces the archive.

Pagination via Telethon's iter_messages (min_id/max_id), checkpointed by
writing each message immediately. Supports both incremental reads (resume
from last written message id) and full reads.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from telethon import functions, types

from .archive import ArchiveWriter, raw_to_json
from .telegram_client import ClientPool


class SourceReader:
    def __init__(self, pool: ClientPool) -> None:
        self.pool = pool

    async def iter_history(self, peer, limit: int | None = None, min_id: int = 0):
        """Yield raw Message objects from account A, newest first."""
        client = self.pool.client("A")
        async for msg in client.iter_messages(peer, limit=limit, min_id=min_id, reverse=False):
            yield msg

    async def read_full(
        self,
        peer,
        run_dir: Path,
        sender_labels: dict[int, str] | None = None,
        download_media: bool = True,
        limit: int | None = None,
    ) -> dict:
        """Read the whole conversation from A into the archive.

        sender_labels maps telegram user id -> "A"/"B" for sender classification.
        Returns the archive meta.
        """
        sender_labels = sender_labels or {}
        writer = ArchiveWriter(run_dir)
        client = self.pool.client("A")
        async for msg in self.iter_history(peer, limit=limit):
            label = sender_labels.get(getattr(msg, "from_id", None) and msg.from_id.user_id)
            cm = writer.write_message(msg, label)
            if download_media and cm.media is not None:
                await writer.download_media(client, msg, cm.media)
        peer_info = await client(functions.users.GetFullUserRequest(peer)) if False else None
        meta = writer.finalize()
        meta["peer"] = raw_to_json(peer)
        meta["read_at"] = datetime.now().isoformat(timespec="seconds")
        with open(run_dir / "archive" / "archive_meta.json", "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        return meta

    async def read_target_snapshot(self, peer, out_path: Path) -> dict:
        """Read B-side history to a simple NDJSON snapshot (target_before/after)."""
        client = self.pool.client("B")
        out = open(out_path, "w", encoding="utf-8")
        n = 0
        async for msg in client.iter_messages(peer, limit=None):
            out.write(
                json.dumps(
                    {
                        "message_id": msg.id,
                        "date": msg.date.isoformat() if msg.date else None,
                        "text": msg.message,
                        "out": bool(msg.out),
                        "media": type(msg.media).__name__ if msg.media else None,
                        "grouped_id": int(msg.grouped_id) if msg.grouped_id else None,
                        "reply_to_msg_id": msg.reply_to.reply_to_msg_id if msg.reply_to else None,
                        "fwd_from": raw_to_json(msg.fwd_from) if msg.fwd_from else None,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            n += 1
        out.close()
        return {"messages": n}
