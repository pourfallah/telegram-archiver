"""Controlled timestamp experiment: import 10 messages spanning 2020-2025
into the test peer, then read the target chat and compare visible dates vs
source dates vs fwd_from metadata.

Run inside the worker container:
    python -m app.services.timestamp_experiment
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# 10 messages, obviously distinct historical dates (per task spec)
EXPERIMENT_MESSAGES = [
    {"id": 1, "date": "2020-01-01T10:00:00+00:00", "sender": "Alice", "text": "msg 2020 new-year"},
    {"id": 2, "date": "2020-01-01T10:01:00+00:00", "sender": "Bob", "text": "msg 2020 one minute later"},
    {"id": 3, "date": "2020-01-02T15:30:00+00:00", "sender": "Alice", "text": "msg 2020 next day"},
    {"id": 4, "date": "2021-06-10T23:59:59+00:00", "sender": "Bob", "text": "msg 2021 june"},
    {"id": 5, "date": "2022-11-15T08:22:41+00:00", "sender": "Alice", "text": "msg 2022 november"},
    {"id": 6, "date": "2023-07-04T12:00:00+00:00", "sender": "Bob", "text": "msg 2023 july"},
    {"id": 7, "date": "2024-02-29T06:30:00+00:00", "sender": "Alice", "text": "msg 2024 leap day"},
    {"id": 8, "date": "2025-05-05T18:45:12+00:00", "sender": "Bob", "text": "msg 2025 may"},
    {"id": 9, "date": "2025-12-31T23:59:59+00:00", "sender": "Alice", "text": "msg 2025 new year eve"},
    {"id": 10, "date": "2019-01-01T00:00:00+00:00", "sender": "Bob", "text": "msg 2019 earliest"},
]


def build_experiment_file(out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for m in sorted(EXPERIMENT_MESSAGES, key=lambda x: x["date"]):
        dt = datetime.fromisoformat(m["date"])
        lines.append(f"{dt.strftime('%d.%m.%Y %H:%M')} - {m['sender']}: {m['text']}")
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path


async def run_experiment(client, peer_identifier: str, workdir: str) -> dict:
    """Full cycle: build file → validate → init → start → re-read target."""
    from telethon.tl.functions import messages as tl_messages

    from app.services.telegram_import import TelegramImporter

    work = Path(workdir)
    import_file = build_experiment_file(work / "experiment_import.txt")

    importer = TelegramImporter(client)
    peer, entity = await importer.resolve_peer(peer_identifier)

    result: dict = {
        "experiment_id": f"ts-exp-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}",
        "peer": getattr(entity, "username", None) or getattr(entity, "id", None),
        "input_messages": EXPERIMENT_MESSAGES,
        "import_file_sha256": __import__("hashlib").sha256(import_file.read_bytes()).hexdigest(),
        "import_lines": import_file.read_text(encoding="utf-8").splitlines(),
    }

    # 1. checkHistoryImport on the head
    head = "\n".join(import_file.read_text(encoding="utf-8").splitlines()[:100])
    parsed = await client(tl_messages.CheckHistoryImportRequest(import_head=head))
    result["checkHistoryImport"] = {
        "pm": bool(getattr(parsed, "pm", False)),
        "group": bool(getattr(parsed, "group", False)),
        "title": getattr(parsed, "title", None),
    }

    # 2. checkHistoryImportPeer + confirm text
    checked = await client(tl_messages.CheckHistoryImportPeerRequest(peer=peer))
    result["checkHistoryImportPeer_confirm_text"] = str(getattr(checked, "confirm_text", ""))

    # 3. initHistoryImport
    input_file = await client.upload_file(import_file)
    init = await client(
        tl_messages.InitHistoryImportRequest(peer=peer, file=input_file, media_count=0)
    )
    import_id = getattr(init, "id", None)
    result["initHistoryImport_id"] = import_id

    # 4. startHistoryImport
    started = await client(
        tl_messages.StartHistoryImportRequest(peer=peer, import_id=import_id)
    )
    result["startHistoryImport"] = bool(started)

    # 5. Wait briefly for server-side materialization, then read the chat
    await asyncio.sleep(20)

    from app.services.telegram_utils import message_to_dict

    msgs = await client.get_messages(peer, limit=60)
    observed = []
    for m in msgs:
        d = message_to_dict(m)
        fwd = getattr(m, "fwd_from", None)
        entry = {
            "target_message_id": d.get("id"),
            "target_visible_date": d.get("date"),
            "target_text": (d.get("text") or "")[:80],
            "target_sender": (d.get("sender") or {}).get("name"),
            "target_forward_header": None,
        }
        if fwd is not None:
            fdate = getattr(fwd, "date", None)
            entry["target_forward_header"] = {
                "imported": bool(getattr(fwd, "imported", False)),
                "original_date": fdate.isoformat() if fdate else None,
                "from_name": getattr(fwd, "from_name", None),
            }
        observed.append(entry)
    result["target_observed_after_import"] = observed

    # Per-message comparison source vs target
    comparisons = []
    for m in EXPERIMENT_MESSAGES:
        match = next(
            (o for o in observed if o["target_text"].strip() == m["text"]), None
        )
        comparisons.append({
            "source_message_id": m["id"],
            "source_datetime_original": m["date"],
            "source_datetime_epoch": int(datetime.fromisoformat(m["date"]).timestamp()),
            "source_sender": m["sender"],
            "source_order": m["id"],
            "import_datetime_string": datetime.fromisoformat(m["date"]).strftime("%d.%m.%Y %H:%M"),
            "import_datetime_epoch": int(datetime.fromisoformat(m["date"]).timestamp()),
            "target_message_id": match["target_message_id"] if match else None,
            "target_visible_date": match["target_visible_date"] if match else None,
            "target_imported_original_date": (
                (match["target_forward_header"] or {}).get("original_date") if match else None
            ),
            "visible_equals_source": bool(match) and (match["target_visible_date"] or "")[:16]
            == m["date"][:16],
            "metadata_equals_source": bool(match)
            and ((match.get("target_forward_header") or {}).get("original_date") or "")[:16]
            == m["date"][:16],
        })
    result["comparison"] = comparisons

    n_vis = sum(1 for c in comparisons if c["visible_equals_source"])
    n_meta = sum(1 for c in comparisons if c["metadata_equals_source"])
    result["summary"] = {
        "found_in_target": sum(1 for c in comparisons if c["target_message_id"]),
        "visible_dates_historical": n_vis,
        "metadata_dates_historical": n_meta,
        "conclusion": (
            "Telegram displays imported messages at import time; historical date "
            "preserved only in fwd_from metadata."
            if n_vis == 0 and n_meta > 0
            else "see per-message rows"
        ),
    }
    return result


async def main() -> None:  # pragma: no cover - manual experiment runner
    import redis.asyncio as aioredis

    from app.config import get_settings
    from app.database import async_session_factory
    from app.models import TelegramSession
    from app.services.session_manager import SessionManager

    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    manager = SessionManager(settings=settings, redis=redis)
    async with async_session_factory() as db:
        from sqlalchemy import select

        acc = await db.scalar(select(TelegramSession).where(TelegramSession.id == 2))
    client, release = await manager.acquire_client(acc)
    try:
        result = await run_experiment(client, "pourfallah", "/data/exports/experiments")
    finally:
        await release()
    out = Path("/data/exports/experiments/TIMESTAMP_EXPERIMENT_RESULT.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result["summary"], indent=2))
    print("full result:", out)


if __name__ == "__main__":
    asyncio.run(main())
