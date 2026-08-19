"""Dashboard authentication API tests."""
from sqlalchemy import select

from app.core.crypto import hash_password
from app.models import AuditLog, UserAccount
from tests.conftest import TEST_ADMIN_PASSWORD, wait_for_audit


async def test_login_success(client, admin_user):
    resp = await client.post(
        "/api/auth/login",
        json={"email": admin_user.email, "password": TEST_ADMIN_PASSWORD},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] > 0
    assert body["user"]["email"] == admin_user.email
    assert body["user"]["is_admin"] is True


async def test_login_wrong_password(client, admin_user):
    resp = await client.post(
        "/api/auth/login",
        json={"email": admin_user.email, "password": "wrong-password"},
    )
    assert resp.status_code == 401


async def test_login_unknown_user(client):
    resp = await client.post(
        "/api/auth/login",
        json={"email": "nobody@example.com", "password": "x"},
    )
    assert resp.status_code == 401


async def test_login_disabled_user(client, db_session):
    user = UserAccount(
        email="disabled@example.com",
        password_hash=hash_password("pw"),
        is_active=False,
    )
    db_session.add(user)
    await db_session.commit()

    resp = await client.post(
        "/api/auth/login", json={"email": user.email, "password": "pw"}
    )
    assert resp.status_code == 403


async def test_email_matches_case_insensitively(client, admin_user):
    resp = await client.post(
        "/api/auth/login",
        json={"email": admin_user.email.upper(), "password": TEST_ADMIN_PASSWORD},
    )
    assert resp.status_code == 200


async def test_protected_endpoint_requires_token(client):
    resp = await client.get("/api/accounts")
    assert resp.status_code == 401


async def test_protected_endpoint_rejects_garbage_token(client):
    resp = await client.get("/api/accounts", headers={"Authorization": "Bearer not-a-jwt"})
    assert resp.status_code == 401


async def test_health_stays_public(client):
    assert (await client.get("/health")).status_code == 200


async def test_stats_requires_auth(client):
    assert (await client.get("/api/stats")).status_code == 401


async def test_login_writes_audit_entry(client, admin_user, db_session):
    await client.post(
        "/api/auth/login",
        json={"email": admin_user.email, "password": TEST_ADMIN_PASSWORD},
    )
    await wait_for_audit(db_session, "post.login")
    row = await db_session.scalar(
        select(AuditLog).where(AuditLog.action == "post.login")
    )
    assert row is not None
    assert row.user_account_id == admin_user.id
    assert row.detail["path"] == "/api/auth/login"
    assert row.detail["status"] == 200


async def test_audit_redacts_sensitive_fields(client, admin_user, db_session):
    await client.post(
        "/api/auth/login",
        json={"email": admin_user.email, "password": TEST_ADMIN_PASSWORD},
    )
    await wait_for_audit(db_session, "post.login")
    row = await db_session.scalar(
        select(AuditLog).where(AuditLog.action == "post.login")
    )
    # The middleware never records request bodies, so the password simply
    # must not be present anywhere in the stored detail.
    assert TEST_ADMIN_PASSWORD not in str(row.detail)
