"""Celery task for real Telegram MTProto history import."""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
from pathlib import Path

import redis.asyncio as aioredis

from app.config import get_settings
from app.models import ChatExport, ImportJob, TelegramSession
from app.services.import_serializer import build_import_file, parse_import_head
from app.services.import_verification import load_canonical_messages, run_verification, write_report
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

            # Build a map of filename -> media info, but ONLY for files actually
            # referenced in the import file (the <attached: FILENAME> lines).
            # Avoids uploading the whole archive's media dir for a sliced import.
            archive_dir = export_dir / "archive"
            media_src = export_dir / "media"
            if archive_dir.exists():
                media_src = archive_dir / "media"

            wanted: set[str] = set()
            import_text = import_file.read_text(encoding="utf-8")
            import re as _re
            for m in _re.finditer(r"<attached:\s*([^>]+)>", import_text):
                wanted.add(m.group(1).strip())

            media_map: dict[str, dict] = {}
            if media_src.exists():
                for media_type_dir in media_src.iterdir():
                    if not media_type_dir.is_dir():
                        continue
                    for media_file in media_type_dir.iterdir():
                        if media_file.is_file() and media_file.name in wanted:
                            media_map.setdefault(media_file.name, {
                                "path": media_file,
                                "type": media_type_dir.name,
                                "mime": _guess_mime(media_file),
                            })
            # If nothing matched by name (e.g. archive filenames differ from the
            # marker), still fall back to every file so media isn't silently dropped.
            if not media_map and wanted:
                media_map.clear()
                for media_type_dir in media_src.iterdir():
                    if media_type_dir.is_dir():
                        for media_file in media_type_dir.iterdir():
                            if media_file.is_file():
                                media_map.setdefault(media_file.name, {
                                    "path": media_file,
                                    "type": media_type_dir.name,
                                    "mime": _guess_mime(media_file),
                                })

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

            # Phase 6b: snapshot target BEFORE start (for new-message delta below)
            before_target_ids: set[int] = set()
            try:
                res0 = await client.get_messages(peer, limit=0)
                n0 = getattr(res0, "total", None)
                if n0 and n0 < 100000:
                    before_list = await client.get_messages(peer, limit=n0)
                    before_target_ids = {int(m.id) for m in before_list}
                else:
                    before_list = await client.get_messages(peer, limit=2000)
                    before_target_ids = {int(m.id) for m in before_list}
            except Exception:  # noqa: BLE001
                before_target_ids = set()
            job.progress = {"phase": "starting_import",
                            "target_before_count": len(before_target_ids)}
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

                # Telegram materializes the historical dates ~1-3 minutes after
                # startHistoryImport. Because the peer may already hold older
                # imported blocks, poll until the imported-message count stops
                # growing (our block + its imports have landed) or a ceiling.
                def _count_imported() -> int:
                    return sum(
                        1 for m in target_list
                        if getattr(m, "fwd_from", None) and getattr(m.fwd_from, "imported", False)
                    )

                prev_count = _count_imported()
                for _ in range(10):  # up to ~5 min
                    await asyncio.sleep(30)
                    target_list = await _fetch_target()
                    cur = _count_imported()
                    if cur > prev_count:  # still growing -> keep waiting
                        prev_count = cur
                        continue
                    # stable population: block finished materializing
                    break

                # Convert to dict format + capture fwd_from (imported) metadata +
                # annotate sender attribution + raw media constructor/attrs.
                from app.services.telegram_utils import message_to_dict
                me0 = await client.get_me()
                expected_sender_id = int(getattr(me0, "id", 0))
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
                    # sender attribution: history import re-maps authors to the
                    # importing account
                    d["expected_sender_id"] = expected_sender_id
                    d["is_new"] = int(m.id) not in before_target_ids

                    # raw media constructor + document attribute names (honest
                    # classification basis)
                    med = getattr(m, "media", None)
                    if med is not None:
                        ctor = type(med).__name__
                        attrs = []
                        mim = None
                        voice = False
                        doc = getattr(med, "document", None)
                        if doc is not None:
                            mim = getattr(doc, "mime_type", None)
                            for a in getattr(doc, "attributes", None) or []:
                                attrs.append(type(a).__name__)
                        d["target_media_raw"] = {
                            "ctor": ctor, "attrs": attrs, "mime": mim,
                            "voice": voice, "round": False,
                        }
                    target_dicts.append(d)

                # Only NEWLY materialized messages are validated (delta).
                new_target_dicts = [d for d in target_dicts if d.get("is_new")]
                target_dicts = new_target_dicts or target_dicts
                # persist snapshots for audit
                try:
                    snap_dir = export_dir / "verification"
                    snap_dir.mkdir(parents=True, exist_ok=True)
                    (snap_dir / "target_snapshot_before.json").write_text(
                        json.dumps({"count": len(before_target_ids),
                                    "ids": sorted(before_target_ids)},
                                   ensure_ascii=False), encoding="utf-8")
                except Exception:  # noqa: BLE001
                    pass

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

                # Maximum-fidelity reports (per-message source/target + reactions)
                try:
                    from app.services.fidelity_reports import (
                        build_fidelity_report as build_fid2,
                    )
                    from app.services.fidelity_reports import (
                        build_reaction_report,
                    )
                    src_map = load_canonical_messages(export_dir / "archive")
                    pok = export_dir / "verification"
                    build_fid2(report, pok, source_messages=src_map, target_messages=target_dicts)
                    build_reaction_report(src_map, pok)
                except Exception:  # noqa: BLE001 — report is additive
                    logger.warning("Max-fidelity report generation failed", exc_info=True)

                # ---- PHASE B: post-import reconstruction (opt-in, safety-gated) ----
                from app.services import reconstruction as recon

                recon_enabled = bool((job.options or {}).get("reconstruct_reactions"))
                src_map = load_canonical_messages(export_dir / "archive")
                # Authoritative mapping comes from the verifier's message_map
                # (multi-field, already computed above).
                mapping = {
                    int(m["source_id"]): {"target_id": m["target_id"],
                                          "match": m.get("match") or "exact",
                                          "source_text": m.get("source_text", "")}
                    for m in report.get("details", {}).get("message_map", [])
                    if m.get("target_id") is not None
                }
                try:
                    me0 = await client.get_me()
                    # Sessions available to this worker (target account is this
                    # client; the source account is a different session that the
                    # worker does not act as unless explicitly wired).
                    available_sessions: set[int] = {int(getattr(me0, "id", 0))}
                    src_me_id = None
                    try:
                        from sqlalchemy import select as _sel

                        async with local_factory() as db2:
                            src_acc = await db2.scalar(
                                _sel(TelegramSession).where(
                                    TelegramSession.id == export.telegram_session_id))
                        if src_acc:
                            # READ-ONLY use of the source session: get_me() only.
                            src_client, src_release = await manager.acquire_client(src_acc)
                            try:
                                src_me = await src_client.get_me()
                                src_me_id = int(getattr(src_me, "id", 0) or 0)
                            finally:
                                await src_release()
                    except Exception:  # noqa: BLE001
                        pass

                    plan = recon.plan_reactions(
                        src_map[-limit:], mapping, available_sessions,
                        source_me_id=src_me_id,
                        target_me_id=int(getattr(me0, "id", 0)),
                    )
                    if recon_enabled:
                        outcomes = await recon.reconstruct_reactions(
                            client, peer, plan,
                            new_target_ids={d.get("id") for d in target_dicts})
                    else:
                        outcomes = [{**p, "outcome": "PLAN_ONLY_DISABLED"} for p in plan]
                    report["reaction_reconstruction"] = {
                        "enabled": recon_enabled,
                        "plan": plan,
                        "outcomes": outcomes,
                        "summary": recon.classify_plan(outcomes),
                    }
                except Exception:  # noqa: BLE001
                    logger.warning("Reaction reconstruction failed", exc_info=True)
                    report["reaction_reconstruction"] = {"enabled": recon_enabled, "error": True}

                # Re-write the report so reaction_reconstruction is included
                write_report(report, report_dir)

                # Reaction recovery + sticker recovery reports
                try:
                    from app.services.fidelity_reports import (
                        build_reaction_recovery_report,
                        build_sticker_recovery_report,
                    )
                    pok = export_dir / "verification"
                    # The VERIFIER's mapping is authoritative for reporting.
                    report_mapping = {
                        int(m["source_id"]): {"target_id": m["target_id"]}
                        for m in report.get("details", {}).get("message_map", [])
                        if m.get("target_id") is not None
                    }
                    build_reaction_recovery_report(
                        src_map, report.get("reaction_reconstruction"), pok)
                    build_sticker_recovery_report(src_map, target_dicts, pok,
                                                  mapping=report_mapping)
                except Exception:  # noqa: BLE001 — report is additive
                    logger.warning("Recovery report generation failed", exc_info=True)

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
