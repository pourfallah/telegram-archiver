"""Export engine — the heart of the suite.

Paced, checkpointed iteration over chat history with live progress metrics,
pause/resume/cancel, flood-wait backoff, and three writers (streaming JSON,
incremental SQLite archive, paged HTML).

Lifecycle states: queued -> running -> completed | failed | paused | cancelled
Crash recovery: every CHECKPOINT_EVERY messages the offset + counters are
committed to PostgreSQL; re-running the task resumes from there.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from telethon.errors import (
    ChannelPrivateError,
    ChatForbiddenError,
    FloodWaitError,
)

from app.config import Settings
from app.models import ChatExport, MediaFile, Message, TelegramSession
from app.services.export_writers import (
    HtmlExportBuilder,
    JsonLineWriter,
    SqliteArchiveBuilder,
    assemble_json_archive,
)
from app.services.media_downloader import MediaDownloader
from app.services.telegram_utils import (
    deserialize_input_peer,
    message_to_dict,
    safe_filename,
)

logger = logging.getLogger(__name__)

BATCH_SIZE = 100
BUILD_BATCH = 2000
MAX_CONSECUTIVE_FLOOD_WAITS = 5
MAX_FETCH_ATTEMPTS = 4


class ExportPaused(Exception):
    pass


class ExportCancelled(Exception):
    pass


class ExportFatal(Exception):
    """Unrecoverable error — export marked failed."""


class ExportEngine:
    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        session_manager,
        redis=None,
        client_factory=None,
        sleep: Any = asyncio.sleep,
        log: Any = logger,
        batch_hook=None,
        downloader=None,
    ) -> None:
        self._settings = settings
        self._sf = session_factory
        self._sm = session_manager
        self._redis = redis
        self._sleep = sleep
        self._log = log
        self._batch_hook = batch_hook  # test hook: async callable(processed) or None
        self._downloader = downloader or MediaDownloader(settings, session_factory)

    # ------------------------------------------------------------ lifecycle

    async def run(self, export_id: int) -> None:
        async with self._sf() as db:
            export = await self._load_export(db, export_id)
            account = export.telegram_session
            if export.status in ("cancelled", "completed"):
                return
            export.status = "running"
            export.started_at = datetime.now(UTC)
            export.error = None
            await db.commit()

        try:
            async with await self._acquire_guard(account) as client:
                await self._run_with_client(export, account, client)
        except ExportPaused:
            self._log.info("Export %s paused by user", export_id)
        except ExportCancelled:
            self._log.info("Export %s cancelled by user", export_id)
        except ExportFatal as exc:
            await self._mark_failed(export_id, str(exc))
            raise
        except Exception as exc:
            await self._mark_failed(export_id, f"{type(exc).__name__}: {exc}")
            raise

    async def _acquire_guard(self, account: TelegramSession):
        """Async context manager yielding the account's connected client (releases after)."""
        client, release = await self._sm.acquire_client(account)

        class _Guard:
            def __init__(self, client, release):
                self._client = client
                self._release = release

            async def __aenter__(self):
                return self._client

            async def __aexit__(self, *exc):
                await self._release()

        return _Guard(client, release)

    async def _load_export(self, db: AsyncSession, export_id: int) -> ChatExport:
        from sqlalchemy.orm import selectinload

        export = await db.scalar(
            select(ChatExport)
            .where(ChatExport.id == export_id)
            .options(selectinload(ChatExport.telegram_session))
        )
        if export is None:
            raise ExportFatal(f"Export {export_id} not found")
        return export

    # ---------------------------------------------------------- core loop

    async def _run_with_client(self, export: ChatExport, account: TelegramSession, client) -> None:
        peer_data = export.options.get("input_peer")
        peer = deserialize_input_peer(peer_data) if peer_data else None
        entity = await client.get_entity(peer or export.chat_id)

        total = await self._total_messages(client, entity)
        export.total_messages_est = total
        async with self._sf() as db:
            await db.commit()

        chat_header = {
            "id": export.chat_id,
            "title": export.chat_title,
            "type": export.chat_type,
            "account": {"phone": account.phone},
        }

        dirs = self._make_dirs(account, export)
        async with self._sf() as db:
            row = await db.get(ChatExport, export.id)
            row.export_dir = str(dirs)
            await db.commit()

        offset_id = export.checkpoint_offset_id or 0
        processed = export.messages_processed
        processed_before = processed
        include_media = export.options.get("include_media", True)

        json_lines = JsonLineWriter(dirs / "messages.jsonl")
        json_lines.open(resume_count=processed_before)

        speed_tracker = _SpeedTracker()
        fetched_since_checkpoint = 0
        consecutive_flood_waits = 0

        try:
            while True:
                batch = await self._fetch_batch(client, entity, offset_id, consecutive_flood_waits)
                consecutive_flood_waits = 0
                if not batch:
                    break

                rows = [message_to_dict(m) for m in batch]
                media_rows = self._collect_media(export, rows)
                offset_id = min(m.id for m in batch)
                processed += self._processed_in(batch)

                json_lines.write_batch(rows)

                # Persist ledger + a DURABLE offset checkpoint in the same
                # transaction, so a crash between batches never re-processes
                # already-committed messages on resume.
                await self._persist_batch(export, rows, media_rows, offset_id, processed)

                if include_media and rows:
                    await self._downloader.download_batch(client, export, dirs, batch, rows)

                if self._batch_hook is not None:
                    await self._batch_hook(processed, export)

                fetched_since_checkpoint += len(batch)
                if fetched_since_checkpoint >= self._settings.checkpoint_every:
                    await self._checkpoint(export, offset_id, processed, speed_tracker)
                    fetched_since_checkpoint = 0
                    if await self._should_stop(export.id):
                        break

                # Pacing: throttle to EXPORT_MSGS_PER_SEC messages/second.
                interval = max(0.0, len(batch) / self._settings.export_msgs_per_sec - 0.05)
                if interval > 0:
                    await self._sleep(interval)

            # Normal completion
            json_lines.close()
            await self._checkpoint(export, offset_id, processed, speed_tracker)
            await self._finalize(export, dirs, json_lines, chat_header)
        except ExportPaused:
            json_lines.close()
            raise
        except ExportCancelled:
            json_lines.close()
            raise
        except Exception:
            json_lines.close()
            raise

    async def _total_messages(self, client, entity) -> int | None:
        try:
            result = await client.get_messages(entity, limit=0)
            total = getattr(result, "total", None)
            # Telegram reports 2147483647 as the total for very large / unknown
            # histories — treat it as unknown so progress is shown as counts
            # (with ETA) rather than a misleading ~0% bar.
            if total in (0, 2**31 - 1):
                return None
            return total
        except Exception:
            return None  # total unknown — progress shown as counts only

    async def _fetch_batch(self, client, entity, offset_id: int, consecutive_flood_waits: int):
        """Fetch one page of messages with flood-wait backoff + retries."""
        attempts = 0
        while True:
            try:
                return await client.get_messages(entity, limit=BATCH_SIZE, offset_id=offset_id)
            except FloodWaitError as exc:
                consecutive_flood_waits += 1
                if consecutive_flood_waits > MAX_CONSECUTIVE_FLOOD_WAITS:
                    raise ExportFatal(
                        f"Telegram kept throttling beyond {MAX_CONSECUTIVE_FLOOD_WAITS} "
                        f"consecutive flood waits (last: {exc.seconds}s)"
                    ) from exc
                self._log.warning("FloodWait %ss — sleeping", exc.seconds)
                await self._sleep(min(int(exc.seconds), 600))
            except (TimeoutError, ConnectionError, OSError) as exc:
                attempts += 1
                if attempts >= MAX_FETCH_ATTEMPTS:
                    raise ExportFatal(f"Network error while fetching messages: {exc}") from exc
                self._log.warning("Transient network error (%s) — retry %d/%d", exc, attempts, MAX_FETCH_ATTEMPTS)
                await self._sleep(2 ** attempts)
            except (ChatForbiddenError, ChannelPrivateError) as exc:
                raise ExportFatal(
                    f"Access to chat {getattr(entity, 'id', '?')} was revoked: {type(exc).__name__}"
                ) from exc

    async def _persist_batch(self, export: ChatExport, rows, media_rows, offset_id: int, processed: int) -> None:
        async with self._sf() as db:
            msg_params = [
                {
                    "chat_export_id": export.id,
                    "message_id": r["id"],
                    "grouped_id": r.get("grouped_id"),
                    "date": _as_aware_datetime(r["date"]),
                    "edit_date": _as_aware_datetime(r["edited"]),
                    "sender_id": (r.get("sender") or {}).get("id"),
                    "sender_name": (r.get("sender") or {}).get("name"),
                    "sender_username": (r.get("sender") or {}).get("username"),
                    "text": r.get("text"),
                    "entities": r.get("entities") or None,
                    "reply_to_message_id": r.get("reply_to"),
                    "forwarded_from": r.get("forwarded_from"),
                    "reactions": r.get("reactions"),
                    "views": r.get("views"),
                    "media_count": len(r.get("media") or []),
                    "media_types": _media_types(r),
                }
                for r in rows
            ]
            if msg_params:
                await db.execute(insert(Message), msg_params)

            media_params = [
                {
                    "chat_export_id": export.id,
                    "message_id": r["id"],
                    "media_type": m["type"],
                    "mime_type": m.get("mime_type"),
                    "size_bytes": m.get("size_bytes"),
                    "original_filename": m.get("original_filename"),
                    "status": "pending",
                    "extra": m.get("extra"),
                }
                for r in rows
                for m in (r.get("media") or [])
            ]
            if media_params:
                await db.execute(insert(MediaFile), media_params)
            # Durable checkpoint: commit the offset + count in the SAME
            # transaction as the batch so a crash never leaves committed
            # messages behind an unreachable offset.
            row = await db.get(ChatExport, export.id)
            if row is not None:
                row.messages_processed = processed
                row.checkpoint_offset_id = offset_id
                row.total_messages_est = export.total_messages_est
                row.checkpoint_updated_at = datetime.now(UTC)
            await db.commit()

    async def _checkpoint(self, export: ChatExport, offset_id: int, processed: int, tracker) -> None:
        speed = tracker.observe(processed)
        total = export.total_messages_est
        # Telegram returns 2147483647 as "unknown / very large" total —
        # keep progress as counts + ETA instead of a misleading ~0% bar.
        if total in (0, 2**31 - 1):
            total = None
        eta = None
        if total and speed > 0 and processed < total:
            remaining = int((total - processed) / speed)
            # eta_seconds is INTEGER in Postgres — clamp absurd values so the
            # checkpoint can't overflow int32 on a large/unknown history.
            eta = remaining if remaining < 2**31 - 1 else None
        export.messages_processed = processed

        async with self._sf() as db:
            row = await db.get(ChatExport, export.id)
            if row is None:
                raise ExportFatal(f"Export {export.id} disappeared during run")
            status = row.status  # may have been changed by the pause/cancel endpoint
            row.messages_processed = processed
            row.files_total = export.files_total
            row.speed_mps = round(speed, 3)
            row.eta_seconds = eta
            row.checkpoint_offset_id = offset_id
            row.checkpoint_updated_at = datetime.now(UTC)
            row.total_messages_est = total
            await db.commit()

        if self._redis is not None:
            payload = {
                "status": status,
                "messages_processed": processed,
                "total_messages_est": total,
                "speed_mps": round(speed, 3),
                "eta_seconds": eta,
            }
            await self._redis.set(f"exports:progress:{export.id}", json.dumps(payload), ex=3600)

        if status == "paused":
            raise ExportPaused
        if status == "cancelled":
            raise ExportCancelled

    async def _should_stop(self, export_id: int) -> bool:
        """Cheap status poll between batches; the authorization happens in
        _checkpoint (which also commits the final state)."""
        async with self._sf() as db:
            status = await db.scalar(select(ChatExport.status).where(ChatExport.id == export_id))
        if status == "paused":
            raise ExportPaused
        if status == "cancelled":
            raise ExportCancelled
        return False

    async def _finalize(self, export: ChatExport, out_dir: Path, json_lines, chat_header) -> None:
        formats = {"json", export.format} if export.format != "all" else {"json", "html", "sqlite"}

        async with self._sf() as db:
            first = await db.scalar(
                select(func.min(Message.date)).where(Message.chat_export_id == export.id)
            )
            last = await db.scalar(
                select(func.max(Message.date)).where(Message.chat_export_id == export.id)
            )
        stats = {
            "messages": export.messages_processed,
            "media": export.files_total,
            "first_date": first.isoformat() if first else None,
            "last_date": last.isoformat() if last else None,
        }

        # 1) Canonical messages.json (assembled from the NDJSON workfile).
        assemble_json_archive(
            lines_path=out_dir / "messages.jsonl",
            out_path=out_dir / "messages.json",
            chat_header=chat_header,
            stats=stats,
        )

        # 2) Optional SQLite archive (media need a flat list; the builder wants
        # each row to carry its message_id).
        if "sqlite" in formats:
            builder = SqliteArchiveBuilder(out_dir / "database.sqlite", chat_header)
            builder.create()
            async for msgs, media in self._ledger_batches(export.id):
                flat = [item for lst in media.values() for item in lst]
                builder.write_batch(msgs, flat)
            builder.finalize(stats)

        # 3) Optional HTML export.
        if "html" in formats:
            builder = HtmlExportBuilder(out_dir, chat_header)
            builder.create()
            async for msgs, media in self._ledger_batches(export.id):
                builder.write_batch(msgs, media)
            builder.finalize(stats)

        async with self._sf() as db:
            row = await db.get(ChatExport, export.id)
            row.status = "completed"
            row.finished_at = datetime.now(UTC)
            await db.commit()
        self._log.info("Export %d completed (%d messages, %d media)",
                       export.id, export.messages_processed, export.files_total)

    async def _ledger_batches(self, export_id: int):
        """Yield (message_dicts, media_by_message_id) from the Postgres ledger."""
        from sqlalchemy import select

        offset = 0
        while True:
            async with self._sf() as db:
                rows = (
                    await db.scalars(
                        select(Message)
                        .where(Message.chat_export_id == export_id)
                        .order_by(Message.message_id.desc())
                        .offset(offset)
                        .limit(BUILD_BATCH)
                    )
                ).all()
                if not rows:
                    return
                msg_ids = [r.message_id for r in rows]
                media_rows = (
                    await db.scalars(
                        select(MediaFile).where(
                            MediaFile.chat_export_id == export_id,
                            MediaFile.message_id.in_(msg_ids),
                        )
                    )
                ).all()
            offset += len(rows)

            msgs = [
                {
                    "message_id": r.message_id,
                    "date": r.date,
                    "edit_date": r.edit_date,
                    "sender_id": r.sender_id,
                    "sender_name": r.sender_name,
                    "sender_username": r.sender_username,
                    "text": r.text,
                    "entities": r.entities,
                    "reply_to_message_id": r.reply_to_message_id,
                    "forwarded_from": r.forwarded_from,
                    "reactions": r.reactions,
                    "views": r.views,
                    "forwards": getattr(r, "forwards", None),
                    "media": r.media_types,
                }
                for r in rows
            ]
            media_by_msg: dict[int, list[dict]] = {}
            for m in media_rows:
                media_by_msg.setdefault(m.message_id, []).append(
                    {
                        "message_id": m.message_id,
                        "media_type": m.media_type,
                        "mime_type": m.mime_type,
                        "size_bytes": m.size_bytes,
                        "original_filename": m.original_filename,
                        "filename": m.original_filename or "file",
                        "sha256": m.sha256,
                        "file_path": m.file_path,
                    }
                )
            yield msgs, media_by_msg

    async def _mark_failed(self, export_id: int, error: str) -> None:
        async with self._sf() as db:
            row = await db.get(ChatExport, export_id)
            if row is None:
                return
            row.status = "failed"
            row.error = error
            row.finished_at = datetime.now(UTC)
            await db.commit()
        self._log.error("Export %d failed: %s", export_id, error)

    # ------------------------------------------------------------- helpers

    def _make_dirs(self, account: TelegramSession, export: ChatExport) -> Path:
        root = self._settings.exports_dir / safe_filename(str(account.phone) or "account")
        chat_name = safe_filename(export.chat_title or f"chat_{export.chat_id}", f"chat_{export.chat_id}")
        out = root / chat_name
        export.export_dir = str(out)
        return out

    @staticmethod
    def _collect_media(export: ChatExport, rows):
        media_rows = []
        for r in rows:
            for m in r.get("media") or []:
                media_rows.append(
                    (
                        r["id"],
                        m["type"],
                        m.get("mime_type"),
                        m.get("size_bytes"),
                        m.get("original_filename"),
                        m.get("filename"),
                        None,  # sha256 (Phase 4 downloader)
                        None,  # file_path
                    )
                )
        export.files_total += len(media_rows)
        return media_rows

    @staticmethod
    def _processed_in(batch) -> int:
        return len(batch)

    async def shutdown(self) -> None:
        pass  # tasks are short-lived; each run closes its own resources


class _SpeedTracker:
    """Exponentially-weighted moving average of messages-per-second."""

    def __init__(self, window: int = 10) -> None:
        self._points: list[tuple[float, int]] = []
        self._window = window

    def observe(self, processed: int) -> float:
        now = time.monotonic()
        self._points.append((now, processed))
        if len(self._points) > self._window * 2:
            self._points = self._points[-self._window:]
        if len(self._points) < 2:
            return 0.0
        t0, c0 = self._points[0]
        t1, c1 = self._points[-1]
        dt = t1 - t0
        if dt <= 0:
            return 0.0
        return max(0.0, (c1 - c0) / dt)


def _as_aware_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _media_types(row: dict[str, Any]) -> dict[str, int] | None:
    media = row.get("media") or []
    if not media:
        return None
    counts: dict[str, int] = {}
    for m in media:
        counts[m["type"]] = counts.get(m["type"], 0) + 1
    return counts
