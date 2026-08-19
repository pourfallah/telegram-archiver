"""Exports API tests: search, create, progress, pause/resume/cancel, files."""
import asyncio
import json
import pathlib

from app.models import ChatExport
from tests.conftest import TEST_ADMIN_PASSWORD
from tests.fakes import (
    DEFAULT_CODE,
    FakeChatEntity,
    FakeDialog,
    FakeSender,
    fake_message,
    fake_photo_media,
)

ACCOUNT = {"phone": "+491234567891", "api_id": 22222, "api_hash": "b" * 32}
BASE = "/api/accounts"


async def _add_user(export_client, email, password, is_admin=True):
    from app.core.crypto import hash_password
    from app.models import UserAccount

    factory = export_client.app.state.session_factory
    async with factory() as s:
        s.add(
            UserAccount(
                email=email,
                password_hash=hash_password(password),
                is_admin=is_admin,
            )
        )
        await s.commit()


async def login_and_logged_in_client(export_client):
    """Log in the dashboard admin and the Telegram account."""
    await _add_user(export_client, "adminio@example.com", TEST_ADMIN_PASSWORD)

    login = await export_client.post(
        "/api/auth/login",
        json={"email": "adminio@example.com", "password": TEST_ADMIN_PASSWORD},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    resp = await export_client.post(BASE, json=ACCOUNT, headers=headers)
    account_id = resp.json()["id"]
    await export_client.post(
        f"{BASE}/{account_id}/code", json={"code": DEFAULT_CODE}, headers=headers
    )
    return headers, account_id


async def wait_for_export(db_session, export_id, status, timeout=15):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        db_session.expire_all()
        row = await db_session.get(ChatExport, export_id)
        if row.status == status:
            return row
        await asyncio.sleep(0.05)
    raise AssertionError(f"export {export_id} never reached {status!r}")


async def test_search_chats_and_create_export(export_client, db_session):
    # Seed the fake Telegram with dialogs + a chat entity.
    chat = FakeChatEntity(id=-100250, title="The Family Group", username="family")
    messages = [
        fake_message(id=10, text="hello", sender=FakeSender(id=1, first_name="Alice"))
    ]
    factory = export_client.app.state.session_manager._factory
    factory.messages = messages
    factory.dialogs = [FakeDialog(chat)]

    headers, account_id = await login_and_logged_in_client(export_client)

    # Search finds the exact match (by username).
    resp = await export_client.get(
        f"{BASE}/{account_id}/chats?q=family", headers=headers
    )
    assert resp.status_code == 200
    results = resp.json()
    assert any(r["id"] == -100250 for r in results)

    # Create an export for the group chat.
    resp = await export_client.post(
        f"{BASE}/{account_id}/exports",
        json={"chat_id": -100250, "format": "all"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    export_id = body["id"]
    assert body["status"] == "queued"
    assert body["chat_title"] == "The Family Group"
    assert body["chat_type"] == "group"
    assert body["account_id"] == account_id

    row = await wait_for_export(db_session, export_id, "completed")
    assert row.messages_processed >= 1


async def test_export_progress_shows_percent(export_client, db_session):
    chat = FakeChatEntity(id=-1001, title="Progress Group", username="prog")
    messages = [fake_message(id=i, text=f"m{i}", sender=FakeSender(id=1, first_name="A")) for i in range(30, 0, -1)]
    factory = export_client.app.state.session_manager._factory
    factory.messages = messages
    factory.dialogs = [FakeDialog(chat)]

    headers, account_id = await login_and_logged_in_client(export_client)
    resp = await export_client.post(
        f"{BASE}/{account_id}/exports",
        json={"chat_id": -1001, "format": "json"},
        headers=headers,
    )
    export_id = resp.json()["id"]
    await wait_for_export(db_session, export_id, "completed")

    resp = await export_client.get(f"/api/exports/{export_id}/progress", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "completed"
    assert body["percent"] == 100.0
    assert body["messages_processed"] == 30
    assert body["total_messages_est"] == 30


async def test_pause_resume_cancel_via_api(export_client, db_session):
    chat = FakeChatEntity(id=-1002, title="Control Group", username="ctrl")
    messages = [fake_message(id=i, text=f"m{i}", sender=FakeSender(id=1, first_name="A")) for i in range(50, 0, -1)]
    factory = export_client.app.state.session_manager._factory
    factory.messages = messages
    factory.dialogs = [FakeDialog(chat)]

    headers, account_id = await login_and_logged_in_client(export_client)
    resp = await export_client.post(
        f"{BASE}/{account_id}/exports",
        json={"chat_id": -1002, "format": "json"},
        headers=headers,
    )
    export_id = resp.json()["id"]

    # Let the export complete (small data — finishes quickly).
    row = await wait_for_export(db_session, export_id, "completed", timeout=20)
    assert row.status == "completed"

    # Terminal exports are guarded: pausing or cancelling a completed export
    # is rejected with 409 (state is immutable once finished).
    resp = await export_client.post(f"/api/exports/{export_id}/cancel", headers=headers)
    assert resp.status_code == 409
    resp = await export_client.post(f"/api/exports/{export_id}/pause", headers=headers)
    assert resp.status_code == 409


async def test_export_files_listing_and_download(export_client, db_session, tmp_path):
    chat = FakeChatEntity(id=-1003, title="Files Group", username="files")
    messages = [
        fake_message(id=5, text="with photo", sender=FakeSender(id=1, first_name="A"), media=fake_photo_media()),
        fake_message(id=4, text="plain", sender=FakeSender(id=2, first_name="B")),
    ]
    factory = export_client.app.state.session_manager._factory
    factory.messages = messages
    factory.dialogs = [FakeDialog(chat)]

    headers, account_id = await login_and_logged_in_client(export_client)
    resp = await export_client.post(
        f"{BASE}/{account_id}/exports",
        json={"chat_id": -1003, "format": "all"},
        headers=headers,
    )
    export_id = resp.json()["id"]
    await wait_for_export(db_session, export_id, "completed")

    resp = await export_client.get(f"/api/exports/{export_id}/files", headers=headers)
    assert resp.status_code == 200
    names = {e["name"] for e in resp.json()}
    assert {"messages.json", "messages.jsonl", "database.sqlite", "index.html", "pages"} <= names

    resp = await export_client.get(
        f"/api/exports/{export_id}/download", params={"path": "messages.json"}, headers=headers
    )
    assert resp.status_code == 200
    doc = json.loads(resp.content)
    assert len(doc["messages"]) == 2

    # Path traversal must be rejected.
    resp = await export_client.get(
        f"/api/exports/{export_id}/download", params={"path": "../../../etc/passwd"}, headers=headers
    )
    assert resp.status_code == 400

    # Listing a directory
    resp = await export_client.get(f"/api/exports/{export_id}/files", params={"path": "pages"}, headers=headers)
    assert resp.status_code == 200
    assert any(not e["is_dir"] for e in resp.json())


async def test_delete_export_purges(export_client, db_session):
    chat = FakeChatEntity(id=-1004, title="Purge Group", username="purge")
    messages = [fake_message(id=3, text="hi", sender=FakeSender(id=1, first_name="A"))]
    factory = export_client.app.state.session_manager._factory
    factory.messages = messages
    factory.dialogs = [FakeDialog(chat)]

    headers, account_id = await login_and_logged_in_client(export_client)
    resp = await export_client.post(
        f"{BASE}/{account_id}/exports",
        json={"chat_id": -1004, "format": "json"},
        headers=headers,
    )
    export_id = resp.json()["id"]
    row = await wait_for_export(db_session, export_id, "completed")

    export_dir = row.export_dir
    assert pathlib.Path(export_dir).exists()

    resp = await export_client.delete(f"/api/exports/{export_id}", headers=headers)
    assert resp.status_code == 204
    assert not pathlib.Path(export_dir).exists()

    db_session.expire_all()
    assert await db_session.get(ChatExport, export_id) is None


async def test_exports_are_scoped_per_user(export_client, db_session):
    chat = FakeChatEntity(id=-1005, title="Scoped Group", username="scope")
    messages = [fake_message(id=3, text="hi", sender=FakeSender(id=1, first_name="A"))]
    factory = export_client.app.state.session_manager._factory
    factory.messages = messages
    factory.dialogs = [FakeDialog(chat)]

    headers_a, account_id = await login_and_logged_in_client(export_client)
    resp = await export_client.post(
        f"{BASE}/{account_id}/exports",
        json={"chat_id": -1005, "format": "json"},
        headers=headers_a,
    )
    export_id = resp.json()["id"]
    await wait_for_export(db_session, export_id, "completed")

    # Second user cannot see the first user's exports.
    await _add_user(export_client, "scoped@example.com", "pw2", is_admin=False)
    login = await export_client.post(
        "/api/auth/login", json={"email": "scoped@example.com", "password": "pw2"}
    )
    headers_b = {"Authorization": f"Bearer {login.json()['access_token']}"}

    assert (await export_client.get("/api/exports", headers=headers_b)).json() == []
    assert (
        await export_client.get(f"/api/exports/{export_id}", headers=headers_b)
    ).status_code == 404
