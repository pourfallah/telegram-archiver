"""Migration + import API tests: convert a completed export, test package, validate."""
from pathlib import Path

from sqlalchemy import select

from app.models import ImportPackage, MigrationJob
from tests.fakes import FakeChatEntity, FakeDialog, FakeSender, fake_message, fake_photo_media
from tests.test_exports_api import (
    BASE,
    login_and_logged_in_client,
    wait_for_export,
)

TEST_PHONE_DIR = "_491234567891"


def _seed_chat(export_client, chat_id, title, username, text="hi"):
    chat = FakeChatEntity(id=chat_id, title=title, username=username)
    messages = [
        fake_message(id=3, text=text, sender=FakeSender(id=1, first_name="Alice")),
        fake_message(id=2, text="", sender=FakeSender(id=1, first_name="Alice"), media=fake_photo_media()),
    ]
    factory = export_client.app.state.session_manager._factory
    factory.messages = messages
    factory.dialogs = [FakeDialog(chat)]


async def _make_completed_export(export_client, db_session, chat_id):
    headers, account_id = await login_and_logged_in_client(export_client)
    resp = await export_client.post(
        f"{BASE}/{account_id}/exports", json={"chat_id": chat_id, "format": "all"}, headers=headers
    )
    export_id = resp.json()["id"]
    await wait_for_export(db_session, export_id, "completed")
    return headers, export_id


async def test_migrate_export_to_whatsapp_package(export_client, db_session):
    _seed_chat(export_client, -10010, "Migration Group", "migr")
    headers, export_id = await _make_completed_export(export_client, db_session, -10010)

    resp = await export_client.post("/api/migrations", json={"export_id": export_id}, headers=headers)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "completed"
    assert body["messages_converted"] == 2

    job = await db_session.get(MigrationJob, body["id"])
    assert (Path(job.output_dir) / "_chat.txt").exists()

    pkg = (await db_session.scalar(select(ImportPackage).order_by(ImportPackage.id.desc())))
    assert pkg is not None
    assert pkg.messages_count == 2

    # Validation
    resp = await export_client.post("/api/import/validate", json={"package_id": pkg.id}, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["validation_status"] == "valid"

    # Instructions
    resp = await export_client.get(f"/api/import/{pkg.id}/instructions", headers=headers)
    assert resp.status_code == 200
    assert any("Import from WhatsApp" in s["detail"] for s in resp.json()["instructions"])


async def test_create_and_validate_test_package(export_client, db_session):
    headers, _ = await login_and_logged_in_client(export_client)
    resp = await export_client.post("/api/migrations/test", json={"count": 50}, headers=headers)
    assert resp.status_code == 201, resp.text
    pkg = resp.json()
    assert pkg["messages_count"] == 50
    assert pkg["media_count"] > 0

    resp = await export_client.post("/api/import/validate", json={"package_id": pkg["id"]}, headers=headers)
    assert resp.json()["validation_status"] == "valid"
    assert resp.json()["stats"]["media"] > 0
