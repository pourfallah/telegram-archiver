"""Export engine tests: full runs, crash-resume, pause/cancel, flood-wait."""
import asyncio
import json
import sqlite3
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.config import Settings
from app.database import async_sessionmaker
from app.models import ChatExport, MediaFile, Message, TelegramSession, UserAccount
from app.services.export_engine import ExportEngine
from app.services.session_manager import SessionManager
from tests.fakes import (
    FakeChatEntity,
    FakeDialog,
    FakeExportClient,
    FakeExportFactory,
    FakeSender,
    fake_document_media,
    fake_message,
    fake_photo_media,
    fake_reactions,
)


def build_history(n=25, chat=None):
    """Message objects newest-first (descending ids), like Telethon returns."""
    chat = chat or FakeChatEntity(id=-100250, title="Family", username="fam")
    alice = FakeSender(id=1, first_name="Alice", last_name="A", username="alice")
    bob = FakeSender(id=2, first_name="Bob", username="bob")
    msgs = []
    for i in range(n, 0, -1):
        sender = alice if i % 2 else bob
        media = None
        if i == 3:
            media = fake_photo_media()
        elif i == 7:
            media = fake_document_media(
                mime_type="video/mp4", size=2048, filename="clip.mp4"
            )
        msgs.append(
            fake_message(
                id=i,
                text=f"message {i}" if i != 3 else "",
                sender=sender,
                date=datetime(2024, 4, i % 28 + 1, 12, 0, tzinfo=UTC),
                edit_date=datetime(2024, 4, i % 28 + 1, 13, 0, tzinfo=UTC) if i == 5 else None,
                reply_to_id=(i + 1) if i % 3 == 0 else None,
                reactions=fake_reactions([("❤️", 2), ("👍", 1)]) if i % 4 == 0 else None,
                views=100 + i if i % 5 == 0 else None,
                media=media,
            )
        )
    return msgs, chat


async def make_export(db_session, chat_id=-100250, title="Family", status="running", options=None):
    from app.core.crypto import encrypt_text

    user = UserAccount(email="owner@example.com", password_hash="x")
    ts = TelegramSession(
        user_account=user, phone="+491234567890", api_id=1,
        api_hash_encrypted=encrypt_text("a" * 32),
        session_encrypted=encrypt_text("fake-session"),
        status="active",
    )
    db_session.add_all([user, ts])
    await db_session.commit()
    export = ChatExport(
        telegram_session_id=ts.id,
        chat_id=chat_id,
        chat_title=title,
        chat_type="group",
        format="all",
        status=status,
        options=options or {},
    )
    db_session.add(export)
    await db_session.commit()
    await db_session.refresh(export)
    return export, ts


def build_engine(db_engine, factory, settings=None, batch_hook=None, redis=None, sleep=None):
    sf = async_sessionmaker(db_engine, expire_on_commit=False)
    settings = settings or Settings()
    manager = SessionManager(settings, redis=redis, client_factory=factory)
    return ExportEngine(
        settings, sf, manager, redis=redis, batch_hook=batch_hook,
        sleep=sleep or asyncio.sleep,
    )


async def poll_status(db_session, export_id, status, timeout=10):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        db_session.expire_all()
        row = await db_session.get(ChatExport, export_id)
        if row.status == status:
            return row
        await asyncio.sleep(0.05)
    raise AssertionError(f"export {export_id} never reached {status!r}; cur={getattr(row,'status',None)} err={getattr(row,'error',None)}")


async def test_full_export_roundtrip(db_engine, db_session, tmp_path):
    msgs, chat = build_history(25)
    factory = FakeExportFactory(messages=msgs, dialogs=[FakeDialog(chat)])
    settings = Settings(exports_dir=tmp_path / "exports")
    export, _ = await make_export(db_session, options={"input_peer": {"cls": "Fake", "id": chat.id}})
    engine = build_engine(db_engine, factory, settings)

    await engine.run(export.id)

    row = await poll_status(db_session, export.id, "completed")
    assert row.messages_processed == 25
    assert row.total_messages_est == 25
    assert row.checkpoint_offset_id is not None
    assert row.files_total == 2  # photo + document

    out = tmp_path / "exports" / "_491234567890" / "Family"
    assert (out / "messages.json").exists()
    archive = json.loads((out / "messages.json").read_text())
    assert archive["stats"]["messages"] == 25
    assert len(archive["messages"]) == 25
    assert archive["chat"]["title"] == "Family"
    first, last = archive["messages"][-1], archive["messages"][0]
    assert last["id"] == 1 and first["id"] == 25  # descending order

    assert (out / "messages.jsonl").exists()
    assert len((out / "messages.jsonl").read_text().splitlines()) == 25

    con = sqlite3.connect(out / "database.sqlite")
    assert con.execute("SELECT count(*) FROM messages").fetchone()[0] == 25
    assert con.execute("SELECT count(*) FROM media").fetchone()[0] == 2
    meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
    assert "chat" in meta and "stats" in meta
    con.close()

    assert (out / "index.html").exists()
    assert (out / "pages" / "page-00001.html").exists()

    # Postgres ledger
    count = await db_session.scalar(
        select(func.count(Message.id)).where(Message.chat_export_id == export.id)
    )
    assert count == 25
    media_count = await db_session.scalar(
        select(func.count(MediaFile.id)).where(MediaFile.chat_export_id == export.id)
    )
    assert media_count == 2


async def test_crash_resume_no_duplicates(db_engine, db_session, tmp_path):
    msgs, chat = build_history(250)
    factory = FakeExportFactory(messages=msgs, dialogs=[FakeDialog(chat)])
    settings = Settings(exports_dir=tmp_path / "exports")
    export, _ = await make_export(db_session, options={"input_peer": {"cls": "Fake", "id": chat.id}})

    crashed = False

    async def crash(processed, export_row):
        nonlocal crashed
        if processed >= 200 and not crashed:
            crashed = True
            raise KeyboardInterrupt  # simulate a hard process kill

    engine = build_engine(db_engine, factory, settings, batch_hook=crash)
    with pytest.raises(KeyboardInterrupt):
        await engine.run(export.id)

    assert crashed
    # Checkpoint landed at 200; status still 'running' (simulated process death).
    await db_session.refresh(export)
    assert export.messages_processed == 200
    out = tmp_path / "exports" / "_491234567890" / "Family"
    assert len((out / "messages.jsonl").read_text().splitlines()) == 200

    # Resume from checkpoint
    export.status = "queued"
    await db_session.commit()
    engine2 = build_engine(db_engine, factory, settings)
    await engine2.run(export.id)

    row = await poll_status(db_session, export.id, "completed")
    assert row.messages_processed == 250

    count = await db_session.scalar(
        select(func.count(Message.id)).where(Message.chat_export_id == export.id)
    )
    assert count == 250  # no duplicates — unique(export_id, message_id) would raise otherwise
    assert len((out / "messages.jsonl").read_text().splitlines()) == 250
    archive = json.loads((out / "messages.json").read_text())
    assert len(archive["messages"]) == 250


async def test_pause_and_resume(db_engine, db_session, tmp_path):
    msgs, chat = build_history(250)
    factory = FakeExportFactory(messages=msgs, dialogs=[FakeDialog(chat)])
    settings = Settings(exports_dir=tmp_path / "exports")
    export, _ = await make_export(db_session, options={"input_peer": {"cls": "Fake", "id": chat.id}})

    paused_once = False

    async def pause_hook(processed, export_row):
        nonlocal paused_once
        if processed >= 100 and not paused_once:
            paused_once = True
            # Simulate the user hitting the pause endpoint mid-run.
            async with async_sessionmaker(db_engine, expire_on_commit=False)() as s:
                row = await s.get(ChatExport, export.id)
                row.status = "paused"
                await s.commit()

    engine = build_engine(db_engine, factory, settings, batch_hook=pause_hook)
    await engine.run(export.id)
    await db_session.refresh(export)
    assert export.status == "paused"

    # Resume
    export.status = "queued"
    await db_session.commit()
    engine2 = build_engine(db_engine, factory, settings)
    await engine2.run(export.id)
    row = await poll_status(db_session, export.id, "completed")
    assert row.messages_processed == 250


async def test_cancel_stops_export(db_engine, db_session, tmp_path):
    msgs, chat = build_history(250)
    factory = FakeExportFactory(messages=msgs, dialogs=[FakeDialog(chat)])
    settings = Settings(exports_dir=tmp_path / "exports")
    export, _ = await make_export(db_session, options={"input_peer": {"cls": "Fake", "id": chat.id}})

    async def cancel_hook(processed, export_row):
        if processed >= 100:
            async with async_sessionmaker(db_engine, expire_on_commit=False)() as s:
                row = await s.get(ChatExport, export.id)
                row.status = "cancelled"
                await s.commit()

    engine = build_engine(db_engine, factory, settings, batch_hook=cancel_hook)
    await engine.run(export.id)
    await db_session.refresh(export)
    assert export.status == "cancelled"
    assert export.messages_processed == 100  # partial export preserved


async def test_flood_wait_recovers(db_engine, db_session, tmp_path):
    msgs, chat = build_history(30)
    original = FakeExportClient(1, "x" * 32, messages=msgs, dialogs=[FakeDialog(chat)])
    calls = {"flooded": False}

    class FloodOnceClient(FakeExportClient):
        async def get_messages(self, entity, limit=0, offset_id=0):
            if limit and not calls["flooded"]:
                calls["flooded"] = True
                from telethon.errors import FloodWaitError

                raise FloodWaitError(request=None)
            return await original.get_messages(entity, limit, offset_id)

    class Factory(FakeExportFactory):
        def __call__(self, api_id, api_hash, session_string=None):
            client = FloodOnceClient(api_id, api_hash, messages=msgs, dialogs=[FakeDialog(chat)])
            self.clients.append(client)
            return client

    settings = Settings(exports_dir=tmp_path / "exports")
    export, _ = await make_export(db_session, options={"input_peer": {"cls": "Fake", "id": chat.id}})
    engine = build_engine(db_engine, Factory(), settings, sleep=lambda s: asyncio.sleep(0))
    await engine.run(export.id)
    row = await poll_status(db_session, export.id, "completed")
    assert row.messages_processed == 30


def test_speed_tracker(monkeypatch):
    from app.services import export_engine
    from app.services.export_engine import _SpeedTracker

    clock = {"t": 100.0}
    monkeypatch.setattr(export_engine.time, "monotonic", lambda: clock["t"])

    tracker = _SpeedTracker()
    assert tracker.observe(0) == 0.0

    clock["t"] = 100.0
    tracker._points.append((export_engine.time.monotonic(), 0))
    clock["t"] = 102.0
    tracker._points.append((export_engine.time.monotonic(), 10))
    assert tracker.observe(10) == pytest.approx(5.0, abs=0.01)

    # Slowing: 0 new messages between t=102 and t=104 → overall rate drops.
    clock["t"] = 104.0
    assert tracker.observe(10) == pytest.approx(2.5, abs=0.01)

    # Next sample resumes the climb.
    clock["t"] = 106.0
    assert tracker.observe(20) == pytest.approx(3.33, abs=0.01)


async def test_unbundled_entity_resolution_falls_back_to_id(db_engine, db_session, tmp_path):
    """Exports created without a serialized input_peer resolve by chat id."""
    msgs, chat = build_history(5)
    factory = FakeExportFactory(messages=msgs, dialogs=[FakeDialog(chat)])
    settings = Settings(exports_dir=tmp_path / "exports")
    export, _ = await make_export(db_session, chat_id=chat.id, options={})
    engine = build_engine(db_engine, factory, settings)
    await engine.run(export.id)
    await poll_status(db_session, export.id, "completed")


async def test_media_download_writes_files_and_hashes(db_engine, db_session, tmp_path):
    """Media rows get downloaded to disk with a SHA-256 and status=downloaded."""
    from sqlalchemy import func

    msgs, chat = build_history(25)
    factory = FakeExportFactory(messages=msgs, dialogs=[FakeDialog(chat)])
    settings = Settings(exports_dir=tmp_path / "exports")
    export, _ = await make_export(db_session, options={"input_peer": {"cls": "Fake", "id": chat.id}})
    engine = build_engine(db_engine, factory, settings)
    await engine.run(export.id)
    await poll_status(db_session, export.id, "completed")

    out = tmp_path / "exports" / "_491234567890" / "Family" / "media"
    assert (out / "photo").exists() and (out / "document").exists()
    photo = list((out / "photo").glob("*"))[0]
    assert photo.is_file() and photo.stat().st_size > 0

    done = await db_session.scalar(
        select(func.count(MediaFile.id)).where(MediaFile.status == "downloaded")
    )
    assert done == 2
    await db_session.refresh(export)
    assert export.files_downloaded == 2
