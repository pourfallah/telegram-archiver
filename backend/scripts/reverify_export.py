"""Re-run export verification with the fixed comparators against live source."""
from __future__ import annotations
import asyncio, json
from pathlib import Path
import redis.asyncio as aioredis
from app.config import get_settings
from app.database import async_session_factory
from app.models import TelegramSession
from app.services.export_verification import verify_export
from app.services.session_manager import SessionManager

EXPORT_DIR = "/data/exports/_989394430100/David Rodriguez/run_15"
A_SESSION_ID = 1
A_VIEW_PEER = 7768075024


async def main():
    settings = get_settings()
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    manager = SessionManager(settings=settings, redis=redis)
    async with async_session_factory() as db:
        from sqlalchemy import select
        acc = await db.scalar(select(TelegramSession).where(TelegramSession.id == A_SESSION_ID))
    client, release = await manager.acquire_client(acc)
    try:
        peer = await client.get_entity(A_VIEW_PEER)
        summary = await verify_export(Path(EXPORT_DIR), client, peer, export_id=15)
        print(json.dumps({k: summary.get(k) for k in
                          ("status", "live_messages", "archive_messages", "checked", "failed_checks")}))
        from collections import Counter
        c = Counter()
        for m in summary.get("per_message", []):
            for f in m.get("failures") or []:
                c[f] += 1
        print("remaining failures:", dict(c))
        if c:
            for m in summary.get("per_message", []):
                if m.get("failures"):
                    print("  msg", m["source_id"], m["failures"])
    finally:
        await release()


asyncio.run(main())