"""Live timeline-materialization experiment.

Imports 2 known messages into the target peer, then reads the target at
0/60/120/180/300/600s and records for each imported message:
  message.date (visible) vs source historical date vs fwd_from.date,
  plus the peer's chronological ordering / total count.

Objective: determine whether Telegram EVER sets message.date to the original
historical instant, or only preserves it in fwd_from.date (import metadata).
"""
import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import redis.asyncio as aioredis

from app.config import get_settings
from app.database import async_session_factory
from app.models import TelegramSession
from app.services.session_manager import SessionManager

TARGET = 165649921          # pourfallah (account B's peer = account A)
SRC_ACCOUNT = 1             # +989394430100
OUT = Path("/data/fidelity/timeline_experiment")
OUT.mkdir(parents=True, exist_ok=True)


def iso(dt) -> str:
    return dt.isoformat()[:19] if dt else None


async def main(run_id: str):
    settings = get_settings()
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    manager = SessionManager(settings=settings, redis=redis)

    async with async_session_factory() as db:
        from sqlalchemy import select
        target = await db.scalar(select(TelegramSession).where(TelegramSession.id == 3))
        src = await db.scalar(select(TelegramSession).where(TelegramSession.id == SRC_ACCOUNT))

    # ---- Build the import file from 2 known source messages ----
    src_dir = Path("/data/exports/_989394430100/RanginKamoon")
    src_archive = src_dir / "archive"
    msgs_path = next((src_archive / "messages").glob("*.ndjson"), None) or src_archive / "messages.ndjson"
    source_msgs = [json.loads(l) for l in msgs_path.read_text().splitlines() if l.strip()]
    # take the 2 most recent text messages with known ids
    picks = [m for m in source_msgs if (m.get("text") or "").strip()][-2:]
    lines = []
    for m in picks:
        ts = m.get("date") or ""
        if isinstance(ts, str) and "T" in ts:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        else:
            dt = datetime.now(UTC)
        dt = dt + timedelta(minutes=3)
        lines.append(f"[{dt.strftime('%d/%m/%Y, %H:%M:%S')}] First Dev.: {m['text']}")
    import_file = OUT / f"{run_id}_import.txt"
    import_file.write_text("\n".join(lines), encoding="utf-8")
    flat_name = f"{run_id}_import.txt"

    # ---- SNAPSHOT BEFORE ----
    client, release = await manager.acquire_client(target)
    peer = await client.get_entity(TARGET)
    before = await client.get_messages(peer, limit=0)
    before_ids = set()
    if before.total:
        # iterate all to be safe (this peer is small)
        before_list = await client.get_messages(peer, limit=200)
        before_ids = {m.id for m in before_list}
    await release()

    (OUT / f"{run_id}_before.json").write_text(json.dumps({"count": len(before_ids), "ids": sorted(before_ids)}, ensure_ascii=False), encoding="utf-8")

    # ---- IMPORT (minimal direct RPC, bypassing the app worker) ----
    client, release = await manager.acquire_client(target)
    try:
        from telethon import functions
        # upload once, reuse the handle for check+init
        handle = await client.upload_file(str(import_file))
        import_head = import_file.read_text(encoding="utf-8").splitlines()[0][:100]
        r = await client(functions.messages.CheckHistoryImportRequest(import_head=import_head))
        print("checkHistoryImport:", type(r).__name__)
        init = await client(functions.messages.InitHistoryImportRequest(
            peer=peer, file=handle,
            media_count=0))
        import_id = getattr(init, "id", None)
        print("initHistoryImport id:", import_id)
        res = await client(functions.messages.StartHistoryImportRequest(
            peer=peer, import_id=import_id))
        print("startHistoryImport:", type(res).__name__, getattr(res, "ok", None))
    finally:
        await release()

    # ---- SNAPSHOT AFTER at intervals ----
    samples = [0, 30, 60, 120, 180, 300, 600]
    report = {"samples": {}, "source_picks": [{ "id": m.get("id"), "text": m.get("text", "")[:30], "date": m.get("date") } for m in picks]}
    for i, delay in enumerate(samples):
        if i > 0:
            await asyncio.sleep(samples[i] - samples[i-1])
        client, release = await manager.acquire_client(target)
        try:
            all_msgs = await client.get_messages(peer, limit=200)
            after_ids = {m.id for m in all_msgs}
            new_ids = after_ids - before_ids
            records = []
            for m in all_msgs:
                if m.id not in new_ids:
                    continue
                fwd = getattr(m, "fwd_from", None)
                fwd_date = getattr(fwd, "date", None) if fwd else None
                imported = bool(getattr(fwd, "imported", False)) if fwd else False
                records.append({
                    "id": m.id,
                    "message_date": iso(m.date),
                    "fwd_from_date": iso(fwd_date),
                    "imported": imported,
                    "text": (m.message or "")[:30],
                })
            # chronological position of newest new message
            position = None
            if records:
                newest_new = min(r["id"] for r in records)
                position = len([m for m in all_msgs if m.id >= newest_new])
            report["samples"][f"{delay}s"] = {
                "new_messages": len(records),
                "total_after": len(after_ids),
                "newest_new_id": min((r["id"] for r in records), default=None),
                "chrono_position_from_top": position,
                "records": sorted(records, key=lambda r: r["id"]),
            }
            print(f"[t={delay}s] new={len(records)} total={len(after_ids)} top_pos={position}",
                  {r["id"]: (r["message_date"], r["fwd_from_date"], r["imported"]) for r in records})
        finally:
            await release()

    (OUT / f"{run_id}_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("WROTE", OUT / f"{run_id}_report.json")


if __name__ == "__main__":
    rid = sys.argv[1] if len(sys.argv) > 1 else "run1"
    asyncio.run(main(rid))
