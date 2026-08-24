"""Live A/B test: dot format vs WhatsApp format media markers.

Imports two tiny files into the test peer and reports, per message, whether
media got attached or the marker stayed as literal text.
Run: docker exec telegram-archiver-worker-1 python -m app.services.media_marker_test
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

TEST_MESSAGES = [
    {"text": "dotfmt text one", "date": datetime(2024, 1, 5, 10, 0, tzinfo=UTC)},
    {"text": "dotfmt text two", "date": datetime(2024, 1, 5, 10, 1, tzinfo=UTC)},
]

MEDIA_FILE = Path("/data/exports/_989394430100/David Rodriguez/archive/media/photo/photo_0.jpg")


def _wa(dt: datetime) -> str:
    return f"{dt.month}/{dt.day}/{dt.year}, {dt.strftime('%I:%M %p').lstrip('0')}"


def build_variants(out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {}

    # Variant A: WhatsApp line format with <attached:> marker
    lines = []
    for m in TEST_MESSAGES:
        lines.append(f"{_wa(m['date'])} - Tester: {m['text']}")
    lines.append(f"{_wa(TEST_MESSAGES[-1]['date'])} - Tester: <attached: {MEDIA_FILE.name}>")
    a = out_dir / "variant_whatsapp.txt"
    a.write_text("\n".join(lines) + "\n", encoding="utf-8")
    files["whatsapp"] = a

    # Variant B: dot format with <attached:> marker (old behaviour)
    lines = []
    for m in TEST_MESSAGES:
        ts = m["date"].strftime("%d.%m.%Y %H:%M")
        lines.append(f"{ts} - Tester: {m['text']}")
    lines.append(
        f"{TEST_MESSAGES[-1]['date'].strftime('%d.%m.%Y %H:%M')} - Tester: "
        f"<attached: {MEDIA_FILE.name}>"
    )
    b = out_dir / "variant_dot.txt"
    b.write_text("\n".join(lines) + "\n", encoding="utf-8")
    files["dot"] = b
    return files


async def run_variant(client, peer, name: str, path: Path) -> dict:
    from telethon.tl.functions import messages as tl

    head = "\n".join(path.read_text(encoding="utf-8").splitlines()[:100])
    await client(tl.CheckHistoryImportRequest(import_head=head))
    input_file = await client.upload_file(path)
    init = await client(tl.InitHistoryImportRequest(peer=peer, file=input_file, media_count=1))
    # upload the media so tokens exist for whichever variant Telegram accepts
    if MEDIA_FILE.exists():
        uploaded = await client.upload_file(MEDIA_FILE)
        from telethon import types

        doc = types.InputMediaUploadedPhoto(file=uploaded)
        try:
            await client(tl.UploadImportedMediaRequest(
                peer=peer, import_id=init.id, file_name=MEDIA_FILE.name, media=doc))
        except Exception:  # noqa: BLE001
            pass
    await client(tl.StartHistoryImportRequest(peer=peer, import_id=init.id))
    before = (await client.get_messages(peer, limit=0)).total
    return {"variant": name, "started": True, "total_before": before}


async def main() -> None:  # noqa: C901
    import redis.asyncio as aioredis

    from app.config import get_settings
    from app.database import async_session_factory
    from app.models import TelegramSession
    from app.services.session_manager import SessionManager

    settings = get_settings()
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    manager = SessionManager(settings=settings, redis=redis)
    async with async_session_factory() as db:
        from sqlalchemy import select

        acc = await db.scalar(select(TelegramSession).where(TelegramSession.id == 3))
    client, release = await manager.acquire_client(acc)
    try:
        peer = await client.get_input_entity("pourfallah")
        variants = build_variants(Path("/data/exports/experiments/media_markers"))
        results = {}
        for name, path in variants.items():
            results[name] = await run_variant(client, peer, name, path)
            await asyncio.sleep(10)

        print(json_dumps(results))

        # Wait for materialization then inspect the tail of the chat
        for i in range(8):
            await asyncio.sleep(20)
            msgs = await client.get_messages(peer, limit=12)
            tail = [
                {
                    "id": m.id,
                    "date": m.date.isoformat()[:16],
                    "imported": bool(getattr(getattr(m, "fwd_from", None), "imported", False)),
                    "text": (m.message or "")[:40],
                    "media": type(m.media).__name__ if m.media else None,
                }
                for m in sorted(msgs, key=lambda x: x.id)
            ]
            imported_tail = [t for t in tail if t["imported"]]
            print(f"--- poll {i + 1}: total={len(tail)} imported_in_tail={len(imported_tail)}")
            for t in tail[-8:]:
                print(t)
            has_dot_media = any(  # noqa
                t["media"] and "dot" in (t["text"] or "").lower() is False and t["imported"] for t in tail
            )
    finally:
        await release()


def json_dumps(obj) -> str:
    import json

    return json.dumps(obj, indent=2, default=str)


if __name__ == "__main__":
    asyncio.run(main())
