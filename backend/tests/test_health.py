"""API smoke tests: health and stats endpoints."""
import pytest


@pytest.mark.asyncio
async def test_health_ok(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["db"] == "up"
    assert body["version"]


@pytest.mark.asyncio
async def test_stats_requires_auth(client):
    resp = await client.get("/api/stats")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_stats_authenticated(client, auth_headers):
    resp = await client.get("/api/stats", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["accounts"] == 0
    assert body["exports_total"] == 0
    assert body["exports_running"] == 0
    assert body["storage_bytes"] >= 0


@pytest.mark.asyncio
async def test_openapi_schema_available(client):
    resp = await client.get("/api/openapi.json")
    assert resp.status_code == 200
    assert "paths" in resp.json()
