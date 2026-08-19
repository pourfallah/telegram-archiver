"""Telegram account login flow API tests (mocked Telethon client)."""
from sqlalchemy import select

from app.core.crypto import decrypt_text
from app.models import TelegramSession
from tests.fakes import DEFAULT_2FA, DEFAULT_CODE

BASE = "/api/accounts"

ACCOUNT = {"phone": "+491234567890", "api_id": 11111, "api_hash": "a" * 32}


async def test_full_login_flow_without_2fa(client, auth_headers, db_session):
    resp = await client.post(BASE, json=ACCOUNT, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    account_id = body["id"]
    assert body["status"] == "auth_pending_code"
    assert body["phone"] == ACCOUNT["phone"]

    resp = await client.post(
        f"{BASE}/{account_id}/code", json={"code": DEFAULT_CODE}, headers=auth_headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "active"

    row = await db_session.scalar(
        select(TelegramSession).where(TelegramSession.id == account_id)
    )
    assert row is not None
    # Session and api_hash must never be stored in plaintext.
    assert row.session_encrypted != "fake-session-11111"
    assert decrypt_text(row.session_encrypted) == "fake-session-11111"
    assert decrypt_text(row.api_hash_encrypted) == ACCOUNT["api_hash"]

    # The flow client should now live in the pool.
    manager = client.app.state.session_manager
    assert account_id in manager._pool


async def test_full_login_flow_with_2fa(client, auth_headers, db_session):
    # Behavior keyed by api_id.
    client.app.state.session_manager = _manager_with_behaviors(
        client, {11112: {"needs_2fa": True}}
    )
    resp = await client.post(
        BASE, json={**ACCOUNT, "api_id": 11112}, headers=auth_headers
    )
    assert resp.status_code == 201, resp.text
    account_id = resp.json()["id"]

    resp = await client.post(
        f"{BASE}/{account_id}/code", json={"code": DEFAULT_CODE}, headers=auth_headers
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "auth_pending_2fa"

    resp = await client.post(
        f"{BASE}/{account_id}/2fa", json={"password": DEFAULT_2FA}, headers=auth_headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "active"


def _manager_with_behaviors(client, behaviors):
    """Swap in a fresh fake factory with the given behaviors."""
    from app.config import get_settings
    from app.services.session_manager import SessionManager
    from tests.fakes import FakeClientFactory

    manager = SessionManager(
        get_settings(),
        redis=client.app.state.redis,
        client_factory=FakeClientFactory(behaviors),
    )
    client.app.state.session_manager = manager
    return manager


async def test_wrong_code_rejected(client, auth_headers):
    resp = await client.post(BASE, json=ACCOUNT, headers=auth_headers)
    account_id = resp.json()["id"]

    resp = await client.post(
        f"{BASE}/{account_id}/code", json={"code": "00000"}, headers=auth_headers
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "invalid_code"

    # Account should still be usable with the correct code afterwards.
    resp = await client.post(
        f"{BASE}/{account_id}/code", json={"code": DEFAULT_CODE}, headers=auth_headers
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "active"


async def test_wrong_2fa_password_rejected(client, auth_headers):
    _manager_with_behaviors(client, {11113: {"needs_2fa": True}})
    resp = await client.post(
        BASE, json={**ACCOUNT, "api_id": 11113}, headers=auth_headers
    )
    account_id = resp.json()["id"]
    await client.post(
        f"{BASE}/{account_id}/code", json={"code": DEFAULT_CODE}, headers=auth_headers
    )
    resp = await client.post(
        f"{BASE}/{account_id}/2fa", json={"password": "nope"}, headers=auth_headers
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "wrong_2fa_password"


async def test_invalid_phone_rejected(client, auth_headers, db_session):
    _manager_with_behaviors(client, {11114: {"invalid_phone": True}})
    resp = await client.post(
        BASE, json={**ACCOUNT, "api_id": 11114}, headers=auth_headers
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "invalid_phone"

    row = await db_session.scalar(
        select(TelegramSession).where(TelegramSession.phone == ACCOUNT["phone"])
    )
    assert row is not None
    assert row.status == "error"


async def test_invalid_api_credentials_rejected(client, auth_headers):
    _manager_with_behaviors(client, {11115: {"invalid_api": True}})
    resp = await client.post(
        BASE, json={**ACCOUNT, "api_id": 11115}, headers=auth_headers
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "invalid_api"


async def test_duplicate_phone_conflict(client, auth_headers):
    r1 = await client.post(BASE, json=ACCOUNT, headers=auth_headers)
    assert r1.status_code == 201
    r2 = await client.post(BASE, json=ACCOUNT, headers=auth_headers)
    assert r2.status_code == 409


async def test_validation_of_create_payload(client, auth_headers):
    resp = await client.post(
        BASE,
        json={"phone": "not-a-phone", "api_id": 1, "api_hash": "x"},
        headers=auth_headers,
    )
    assert resp.status_code == 422


async def test_check_returns_user_report(client, auth_headers):
    await client.post(BASE, json=ACCOUNT, headers=auth_headers)
    account_id = (await client.get(BASE, headers=auth_headers)).json()[0]["id"]
    await client.post(
        f"{BASE}/{account_id}/code", json={"code": DEFAULT_CODE}, headers=auth_headers
    )

    resp = await client.post(
        f"{BASE}/{account_id}/check", headers=auth_headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "active"
    assert body["user"]["username"] == "fakeuser"
    assert body["user"]["premium"] is False


async def test_check_on_unauthenticated_account_fails(client, auth_headers):
    resp = await client.post(BASE, json=ACCOUNT, headers=auth_headers)
    account_id = resp.json()["id"]
    resp = await client.post(
        f"{BASE}/{account_id}/check", headers=auth_headers
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "not_authenticated"


async def test_delete_account(client, auth_headers, db_session):
    resp = await client.post(BASE, json=ACCOUNT, headers=auth_headers)
    account_id = resp.json()["id"]
    resp = await client.delete(f"{BASE}/{account_id}", headers=auth_headers)
    assert resp.status_code == 204
    assert (await client.get(f"{BASE}/{account_id}", headers=auth_headers)).status_code == 404
    assert (
        await db_session.scalar(
            select(TelegramSession).where(TelegramSession.id == account_id)
        )
        is None
    )


async def test_accounts_are_scoped_per_user(client, db_session, auth_headers):
    """User B must not see or touch user A's Telegram accounts."""
    resp = await client.post(BASE, json=ACCOUNT, headers=auth_headers)
    account_id = resp.json()["id"]

    # Second dashboard user.
    from app.core.crypto import hash_password
    from app.models import UserAccount

    other = UserAccount(
        email="other@example.com",
        password_hash=hash_password("other-pass"),
        is_admin=False,
    )
    db_session.add(other)
    await db_session.commit()

    login = await client.post(
        "/api/auth/login", json={"email": "other@example.com", "password": "other-pass"}
    )
    other_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    assert (await client.get(BASE, headers=other_headers)).json() == []
    assert (
        await client.get(f"{BASE}/{account_id}", headers=other_headers)
    ).status_code == 404
    assert (
        await client.delete(f"{BASE}/{account_id}", headers=other_headers)
    ).status_code == 404
    assert (
        await client.post(f"{BASE}/{account_id}/code", json={"code": "1"}, headers=other_headers)
    ).status_code == 404


async def test_list_accounts_returns_created(client, auth_headers):
    await client.post(BASE, json=ACCOUNT, headers=auth_headers)
    resp = await client.get(BASE, headers=auth_headers)
    assert resp.status_code == 200
    bodies = resp.json()
    assert len(bodies) == 1
    assert bodies[0]["phone"] == ACCOUNT["phone"]
    assert bodies[0]["status"] == "auth_pending_code"
