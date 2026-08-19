"""Shared fixtures.

Database strategy:
- If TEST_DATABASE_URL is set (CI / integration), use that engine and
  create/drop all tables around each test module.
- Otherwise fall back to an in-memory SQLite database (aiosqlite),
  which keeps the unit test suite hermetic and fast.
"""
import os

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.database import Base, get_session
from app.main import create_app

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")


@pytest_asyncio.fixture
async def db_engine():
    if TEST_DATABASE_URL:
        engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    else:
        engine = create_async_engine(
            "sqlite+aiosqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
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
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
