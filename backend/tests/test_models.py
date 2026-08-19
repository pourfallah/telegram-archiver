"""Model round-trip tests: every model persists and reads back correctly."""
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.models import (
    AuditLog,
    ChatExport,
    ImportPackage,
    MediaFile,
    Message,
    MigrationJob,
    TelegramSession,
    UserAccount,
)


@pytest.mark.asyncio
async def test_user_account_roundtrip(db_session):
    user = UserAccount(email="admin@example.com", password_hash="hash", is_admin=True)
    db_session.add(user)
    await db_session.commit()

    fetched = await db_session.scalar(select(UserAccount).where(UserAccount.email == "admin@example.com"))
    assert fetched is not None
    assert fetched.password_hash == "hash"
    assert fetched.is_admin is True
    assert fetched.created_at is not None


@pytest.mark.asyncio
async def test_full_export_graph_roundtrip(db_session):
    """UserAccount -> TelegramSession -> ChatExport -> Message + MediaFile."""
    user = UserAccount(email="owner@example.com", password_hash="h")
    session = TelegramSession(
        user_account=user,
        phone="+491234567890",
        api_id=12345,
        api_hash_encrypted="ciphertext",
        session_encrypted="ciphertext",
        status="active",
    )
    db_session.add_all([user, session])
    await db_session.commit()

    export = ChatExport(
        telegram_session_id=session.id,
        chat_id=-100123456789,
        chat_title="Family",
        chat_type="group",
        format="all",
        status="running",
        messages_processed=10,
        checkpoint_offset_id=42,
        options={"include_media": True},
    )
    db_session.add(export)
    await db_session.commit()

    msg = Message(
        chat_export_id=export.id,
        message_id=42,
        date=func.now(),
        sender_id=1,
        sender_name="Alice",
        text="hello",
        entities=[{"type": "bold", "offset": 0, "length": 5}],
        reactions={"❤": 3},
    )
    media = MediaFile(
        chat_export_id=export.id,
        message_id=42,
        media_type="photo",
        mime_type="image/jpeg",
        size_bytes=12345,
        original_filename="photo.jpg",
        status="downloaded",
        sha256="a" * 64,
    )
    db_session.add_all([msg, media])
    await db_session.commit()

    fetched = await db_session.scalar(
        select(ChatExport)
        .where(ChatExport.id == export.id)
        .options(
            selectinload(ChatExport.telegram_session).selectinload(TelegramSession.user_account),
            selectinload(ChatExport.messages),
            selectinload(ChatExport.media_files),
        )
    )
    assert fetched is not None
    assert fetched.telegram_session.phone == "+491234567890"
    assert fetched.telegram_session.user_account.email == "owner@example.com"
    assert fetched.messages[0].text == "hello"
    assert fetched.messages[0].reactions == {"❤": 3}
    assert fetched.media_files[0].sha256 == "a" * 64
    assert fetched.options == {"include_media": True}
    assert fetched.checkpoint_offset_id == 42


@pytest.mark.asyncio
async def test_message_unique_per_export(db_session):
    export = ChatExport(
        telegram_session_id=None,
        chat_id=1,
        chat_title="t",
        chat_type="private",
    )
    # telegram_session_id is NOT NULL — use a real session instead
    user = UserAccount(email="u@example.com", password_hash="h")
    ts = TelegramSession(
        user_account=user,
        phone="+1",
        api_id=1,
        api_hash_encrypted="c",
        status="new",
    )
    db_session.add_all([user, ts])
    await db_session.commit()
    export.telegram_session_id = ts.id
    db_session.add(export)
    await db_session.commit()

    db_session.add(Message(chat_export_id=export.id, message_id=1, date=func.now()))
    await db_session.commit()

    db_session.add(Message(chat_export_id=export.id, message_id=1, date=func.now()))
    with pytest.raises(IntegrityError):
        await db_session.commit()


@pytest.mark.asyncio
async def test_migration_and_import_package_roundtrip(db_session):
    user = UserAccount(email="m@example.com", password_hash="h")
    ts = TelegramSession(
        user_account=user,
        phone="+2",
        api_id=2,
        api_hash_encrypted="c",
        status="active",
    )
    db_session.add_all([user, ts])
    await db_session.commit()

    export = ChatExport(
        telegram_session_id=ts.id,
        chat_id=3,
        chat_title="c",
        chat_type="private",
        status="completed",
    )
    db_session.add(export)
    await db_session.commit()

    job = MigrationJob(chat_export_id=export.id, status="completed", messages_converted=7)
    db_session.add(job)
    await db_session.commit()

    pkg = ImportPackage(
        migration_job_id=job.id,
        name="family-whatsapp",
        package_path="/exports/family",
        format="whatsapp",
        messages_count=7,
        media_count=2,
        users_detected={"1": "Alice", "2": "Bob"},
        validation_status="valid",
        validation_report={"warnings": []},
    )
    db_session.add(pkg)
    await db_session.commit()

    fetched = await db_session.scalar(
        select(ImportPackage)
        .where(ImportPackage.id == pkg.id)
        .options(
            selectinload(ImportPackage.migration_job).selectinload(
                MigrationJob.chat_export
            )
        )
    )
    assert fetched is not None
    assert fetched.users_detected == {"1": "Alice", "2": "Bob"}
    assert fetched.migration_job.chat_export.chat_title == "c"


@pytest.mark.asyncio
async def test_audit_log_roundtrip(db_session):
    user = UserAccount(email="a@example.com", password_hash="h")
    db_session.add(user)
    await db_session.commit()

    log = AuditLog(
        user_account_id=user.id,
        action="export.start",
        resource_type="ChatExport",
        resource_id="7",
        detail={"chat": "Family"},
        ip="127.0.0.1",
    )
    db_session.add(log)
    await db_session.commit()

    fetched = await db_session.scalar(select(AuditLog).where(AuditLog.action == "export.start"))
    assert fetched is not None
    assert fetched.ip == "127.0.0.1"
    assert fetched.detail == {"chat": "Family"}
