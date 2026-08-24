"""Celery task for real Telegram MTProto history import."""

from __future__ import annotations

import asyncio
import logging
import mimetypes
from pathlib import Path

import redis.asyncio as aioredis

from app.config import get_settings
from app.models import ChatExport, ImportJob, TelegramSession
from app.services.import_serializer import build_import_file, parse_import_head
from app.services.import_verification import run_verification, write_report
from app.services.session_manager import SessionManager
from app.services.telegram_import import ImportProtocolError, TelegramImporter
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


def _guess_mime(path: Path) -> str:
    mime, _ = mimetypes.guess_type(path.name)
    return mime or "application/octet-stream"


async def _build_input_media(client, info: dict):
    """Build InputMedia for uploadImportedMedia from media info.

    The file must first be uploaded to Telegram to obtain an InputFile handle.
    """
    from telethon.tl.types import (
        DocumentAttributeFilename,
        InputMediaUploadedDocument,
        InputMediaUploadedPhoto,
    )

    handle = await client.upload_file(info["path"], file_name=info["path"].name)
    mime = info.get("mime") or "application/octet-stream"
    media_type = info.get("type") or "document"

    if media_type == "photo" or mime.startswith("image/"):
        return InputMediaUploadedPhoto(file=handle)
    return InputMediaUploadedDocument(
        file=handle,
        mime_type=mime,
        attributes=[DocumentAttributeFilename(file_name=info["path"].name)],
    )


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)
def run_import(self, job_id: int) -> dict:
    """Run a real Telegram history import job.

    Celery prefork workers run each task under a fresh ``asyncio.run`` loop. The
    global engine in ``app.database`` binds to the first loop that uses it, so
    reusing it across tasks raises "Future attached to a different loop". We
    create a task-local engine + session factory for every run and dispose it
    before exiting (mirrors the export worker fix).
    """
    import asyncio

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    settings = get_settings()
    local_engine = create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        pool_timeout=60,
        echo=False,
    )
    local_factory = async_sessionmaker(local_engine, class_=AsyncSession, expire_on_commit=False)

    async def _go() -> dict:
        try:
            return await _run_import_async(job_id, local_factory)
        finally:
            await local_engine.dispose()

    return asyncio.run(_go())


async def _run_import_async(job_id: int, local_factory) -> dict:
    async with local_factory() as db:
        job = await db.get(ImportJob, job_id)
        if job is None:
            return {"error": "Job not found"}

        try:
            job.status = "validating"
            job.started_at = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
            await db.commit()

            export = await db.get(ChatExport, job.source_export_id)
            account = await db.get(TelegramSession, job.target_account_id)

            if not export or not export.export_dir or not account:
                raise ValueError("Export or account missing")

            # Get target client
            settings = get_settings()
            redis = aioredis.from_url(settings.redis_url, decode_responses=True)
            manager = SessionManager(settings=settings, redis=redis)
            client, release = await manager.acquire_client(account)
            importer = TelegramImporter(client)

            # Resolve target peer — prefer the stored numeric peer id (always
            # available from the wizard's Step 3 selection); fall back to a
            # contact identifier string if provided.
            contact_id = job.options.get("contact_identifier")
            try:
                if job.target_peer_id:
                    entity = await client.get_entity(job.target_peer_id)
                    peer = await client.get_input_entity(entity)
                elif contact_id:
                    peer, entity = await importer.resolve_peer(contact_id)
                else:
                    raise ValueError(
                        "No target_peer_id or contact_identifier in job options"
                    )
            except Exception as exc:
                job.status = "failed"
                job.error = f"Peer resolution failed: {exc}"
                await db.commit()
                await release()
                return {"error": job.error}

            # Phase 1: Peer validation
            job.status = "peer_checking"
            job.progress = {"phase": "peer_checking"}
            await db.commit()

            try:
                peer_check = await importer.check_history_import_peer(peer)
                job.progress = {"phase": "peer_checking", "confirm_text": peer_check.get("confirm_text", "")}
                await db.commit()
            except ImportProtocolError as exc:
                job.status = "failed"
                job.error = f"Peer check failed: {exc.error_code} — {exc.message}"
                await db.commit()
                await release()
                return {"error": job.error}

            # Phase 2: Build import file
            job.status = "import_initialized"
            job.progress = {"phase": "building_import_file"}
            await db.commit()

            export_dir = Path(export.export_dir)
            import_file = export_dir / "import" / "import.txt"
            import_file.parent.mkdir(parents=True, exist_ok=True)

            limit = job.message_limit

            # Detect the target account's UTC offset from a live message date:
            # Telegram returns message.date in the account's local tz as naive
            # datetimes via Telethon (tz-naive when tz_offset unknown). Compare a
            # known recent message's server epoch vs its rendered wall clock.
            tz_offset_minutes = job.options.get("tz_offset_minutes")
            if tz_offset_minutes is None:
                # Telethon returns message.date in UTC — it cannot tell us the
                # TARGET account's display timezone. Default to UTC+3:30 (Iran)
                # which matches this deployment's accounts; overridable per job.
                tz_offset_minutes = 210

            stats = build_import_file(
                export_dir,
                import_file,
                limit=limit,
                tz_offset_minutes=tz_offset_minutes,
            )

            # Phase 3: checkHistoryImport
            job.progress = {"phase": "check_import_format"}
            await db.commit()

            import_head = parse_import_head(import_file)
            try:
                fmt_check = await importer.check_history_import(import_head)
                logger.info(f"Import format check: {fmt_check}")
            except ImportProtocolError as exc:
                job.status = "failed"
                job.error = f"Format check failed: {exc.error_code} — {exc.message}"
                await db.commit()
                await release()
                return {"error": job.error}

            # Phase 4: initHistoryImport
            job.status = "import_initialized"
            job.progress = {"phase": "init_history_import"}
            await db.commit()

            media_count = stats["media_refs"]
            try:
                import_id = await importer.init_history_import(peer, import_file, media_count)
                if import_id is None:
                    raise ValueError("initHistoryImport returned no import_id")
                job.import_id = import_id
                await db.commit()
            except ImportProtocolError as exc:
                job.status = "failed"
                job.error = f"Init import failed: {exc.error_code} — {exc.message}"
                await db.commit()
                await release()
                return {"error": job.error}

            # Phase 5: Upload media
            job.status = "media_uploading"
            job.progress = {"phase": "media_uploading", "uploaded": 0, "total": media_count}
            await db.commit()

            # Build a map of filename -> media info from the archive
            archive_dir = export_dir / "archive"
            media_src = export_dir / "media"
            if archive_dir.exists():
                media_src = archive_dir / "media"

            media_map = {}
            # Scan media directory
            for media_type_dir in media_src.iterdir():
                if media_type_dir.is_dir():
                    for media_file in media_type_dir.iterdir():
                        if media_file.is_file():
                            key = media_file.name
                            if key not in media_map:
                                media_map[key] = {
                                    "path": media_file,
                                    "type": media_type_dir.name,
                                    "mime": _guess_mime(media_file),
                                }

            # Upload each media file
            uploaded_tokens = {}
            for idx, (filename, info) in enumerate(media_map.items()):
                job.progress = {"phase": "media_uploading", "uploaded": idx, "total": media_count, "current_file": filename}
                await db.commit()

                try:
                    # Upload the media file (first to Telegram, then attach to import)
                    media = await _build_input_media(client, info)
                    token = await importer.upload_imported_media(
                        peer, import_id, filename, media
                    )
                    uploaded_tokens[filename] = token
                    logger.info(f"Uploaded media {filename}: {token}")
                except ImportProtocolError as exc:
                    job.status = "failed"
                    job.error = f"Media upload failed for {filename}: {exc.error_code} — {exc.message}"
                    await db.commit()
                    await release()
                    return {"error": job.error}

            # Phase 5b: Media uploaded — Telegram matches tokens to the import
            # file by filename (the <attached: filename> lines), no splicing needed.
            job.progress = {"phase": "media_splicing", "uploaded": media_count, "total": media_count}
            await db.commit()

            # Phase 6: Start import
            job.status = "starting_import"
            job.progress = {"phase": "starting_import"}
            await db.commit()

            try:
                ok = await importer.start_history_import(peer, import_id)
                if not ok:
                    raise ValueError("startHistoryImport returned false")
            except ImportProtocolError as exc:
                job.status = "failed"
                job.error = f"Start import failed: {exc.error_code} — {exc.message}"
                await db.commit()
                await release()
                return {"error": job.error}

            # Phase 7: Verify
            job.status = "verifying"
            job.progress = {"phase": "verifying"}
            await db.commit()

            # Re-read target chat messages for verification
            try:
                # Telegram materializes the historical dates ~1-3 minutes after
                # startHistoryImport returns. Poll until the newest imported
                # message leaves its provisional import-time stamp (or timeout).
                async def _fetch_target():
                    res = await client.get_messages(peer, limit=0)
                    total_n = getattr(res, "total", None)
                    if total_n and total_n < 5000:
                        got = await client.get_messages(peer, limit=total_n)
                        return list(got)
                    got = await client.get_messages(peer, limit=1000)
                    return list(got)

                target_list = await _fetch_target()
                for _ in range(6):  # up to ~3 min of polling
                    recent = [m for m in target_list if getattr(m, "fwd_from", None)
                              and getattr(m.fwd_from, "imported", False)]
                    if not recent:
                        break
                    sample = max(recent, key=lambda m: m.id)
                    if abs((sample.date - __import__("datetime").datetime.now(
                            __import__("datetime").timezone.utc)).total_seconds()) > 300:
                        break  # already historical
                    await asyncio.sleep(30)
                    target_list = await _fetch_target()

                # Convert to dict format + capture fwd_from (imported) metadata
                from app.services.telegram_utils import message_to_dict
                target_dicts = []
                for m in target_list:
                    d = message_to_dict(m)
                    fwd = getattr(m, "fwd_header", None)
                    if fwd is None:
                        fwd = getattr(m, "fwd_from", None)
                    if fwd is not None:
                        fdate = getattr(fwd, "date", None)
                        d["fwd_from"] = {
                            "imported": bool(getattr(fwd, "imported", False)),
                            "date": fdate.isoformat() if fdate else None,
                            "from_name": getattr(fwd, "from_name", None),
                        }
                    target_dicts.append(d)

                # Run verification (only the imported slice of the source)
                export_dir = Path(export.export_dir)
                report = run_verification(
                    export_dir / "archive", target_dicts, imported_count=limit
                )

                # Write verification report
                report_dir = export_dir / "verification"
                write_report(report, report_dir)

                # RECOVERY_FIDELITY_REPORT.html — fidelity scorecard
                try:
                    from app.services.fidelity_report import build_fidelity_report
                    build_fidelity_report(report, export_dir / "verification")
                except Exception:  # noqa: BLE001 — report is additive
                    logger.warning("Fidelity report generation failed", exc_info=True)

                # IMPORT DEBUG LOG — reproducibility record for this job
                import hashlib
                debug_log = {
                    "job_id": job_id,
                    "source_archive": str(Path(export.export_dir) / "archive"),
                    "target_account": account.phone,
                    "target_peer": getattr(entity, "username", None)
                    or getattr(entity, "id", None),
                    "source_message_count": stats["messages"],
                    "media_count_declared": media_count,
                    "media_uploaded": len(uploaded_tokens),
                    "import_file_sha256": hashlib.sha256(
                        import_file.read_bytes()
                    ).hexdigest(),
                    "import_file_size_bytes": import_file.stat().st_size,
                    "first_source_timestamp": stats["date_min"],
                    "last_source_timestamp": stats["date_max"],
                    "checkHistoryImport_result": fmt_check,
                    "initHistoryImport_result": {"ok": True},
                    "import_id": import_id,
                    "startHistoryImport_result": True,
                    "target_message_retrieval_count": len(target_list),
                    "verification_overall": report.get("overall"),
                    "timestamp_analysis": report.get("timestamp_analysis", {}).get(
                        "historical_metadata_preserved"
                    ),
                }
                import json as _json
                (export_dir / "verification" / "IMPORT_DEBUG_LOG.json").write_text(
                    _json.dumps(debug_log, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8",
                )
                logger.info("Import job %s debug log: %s", job_id, debug_log)

                prog = {
                    "phase": "completed",
                    "verification": report,
                    "report_dir": str(report_dir),
                }
                job.progress = prog
                job.status = "completed" if report["overall"] in ("FULL_MATCH", "SOURCE_COVERED_EXTRA_IN_TARGET") else "partial"
            except Exception as exc:
                logger.warning(f"Verification failed: {exc}")
                job.progress = {"phase": "completed", "verification_error": str(exc)}
                job.status = "completed"

            job.finished_at = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
            await db.commit()
            await release()

            return {"job_id": job_id, "status": job.status}

        except Exception as exc:  # noqa: BLE001
            logger.exception("Import job %s failed", job_id)
            job.status = "failed"
            job.error = str(exc)
            job.finished_at = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
            await db.commit()
            return {"error": str(exc)}
