"""Shared fixtures.

Database strategy:
- If TEST_DATABASE_URL is set (CI / integration), use that engine and
  create/drop all tables around each test module.
- Otherwise fall back to an in-memory SQLite database (aiosqlite),
  which keeps the unit test suite hermetic and fast.
"""
import asyncio
import os

# Encryption key for the test process — MUST be set before any `app.*` module
# is imported (Settings is cached at import time). Tests that need a specific
# key override it explicitly via Settings(session_encryption_key=...).
if "SESSION_ENCRYPTION_KEY" not in os.environ:
    from cryptography.fernet import Fernet

    os.environ["SESSION_ENCRYPTION_KEY"] = Fernet.generate_key().decode()

# Fast pacing + small checkpoints for export-engine tests (Settings is cached
# at import time, so these must be set before any app.* import).
os.environ.setdefault("EXPORT_MSGS_PER_SEC", "1000")
os.environ.setdefault("CHECKPOINT_EVERY", "5")

import fakeredis.aioredis
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings, get_settings
from app.core.crypto import hash_password
from app.database import Base, get_session
from app.main import create_app
from app.models import UserAccount
from app.services.rate_limit import FixedWindowLimiter
from app.services.session_manager import SessionManager
from tests.fakes import FakeClientFactory

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")
TEST_ADMIN_PASSWORD = "test-admin-pass-123"


@pytest_asyncio.fixture
async def db_engine(tmp_path):
    """File-based SQLite by default so concurrent sessions (endpoint + audit
    middleware) serialize through the file lock instead of deadlocking on a
    single in-memory connection. Set TEST_DATABASE_URL to run against a real
    PostgreSQL (CI / scripts/test-pg.sh)."""
    if TEST_DATABASE_URL:
        engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    else:
        db_file = tmp_path / "test.db"
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{db_file}",
            connect_args={"timeout": 30},
        )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        yield session


@pytest_asyncio.fixture
async def client(db_engine):
    """API client with the test database wired in via dependency override."""
    app = create_app()
    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    async def override_get_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    app.state.session_factory = factory

    fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    app.state.redis = fake_redis
    app.state.session_manager = SessionManager(
        get_settings(), redis=fake_redis, client_factory=FakeClientFactory()
    )
    app.state.login_limiter = FixedWindowLimiter(fake_redis, 10, 300, "login:test")
    app.state.code_limiter = FixedWindowLimiter(fake_redis, 10, 300, "code:test")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        ac.app = app  # tests reach the app (state, session manager) through the client
        yield ac
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def export_client(db_engine, tmp_path):
    """API client wired to a fake Telegram with message history and an
    in-process export engine (InlineTaskRunner) so exports run hermetically.
    Each test gets an isolated exports directory so appended workfiles never
    leak between tests."""
    from app.services.export_engine import ExportEngine
    from app.services.task_runner import InlineTaskRunner
    from tests.fakes import FakeExportFactory

    app = create_app()
    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    async def override_get_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    app.state.session_factory = factory

    fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    app.state.redis = fake_redis

    fake_factory = FakeExportFactory()
    manager = SessionManager(
        get_settings(), redis=fake_redis, client_factory=fake_factory
    )
    settings = Settings(exports_dir=tmp_path / "exports")
    engine = ExportEngine(settings, factory, manager, redis=fake_redis)
    app.state.session_manager = manager
    app.state.task_runner = InlineTaskRunner(engine)
    app.state.export_engine = engine
    app.state.login_limiter = FixedWindowLimiter(fake_redis, 10, 300, "login:test")
    app.state.code_limiter = FixedWindowLimiter(fake_redis, 10, 300, "code:test")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        ac.app = app
        yield ac
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def admin_user(db_session):
    user = UserAccount(
        email="admin@example.com",
        password_hash=hash_password(TEST_ADMIN_PASSWORD),
        is_admin=True,
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def auth_headers(client, admin_user):
    resp = await client.post(
        "/api/auth/login",
        json={"email": admin_user.email, "password": TEST_ADMIN_PASSWORD},
    )
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def wait_for_audit(db_session, action: str, timeout: float = 2.0) -> None:
    """Poll until the audit middleware's background task has written a row."""
    from app.models import AuditLog

    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        row = await db_session.scalar(
            select(AuditLog).where(AuditLog.action == action)
        )
        if row is not None:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"audit row for action {action!r} was never written")
