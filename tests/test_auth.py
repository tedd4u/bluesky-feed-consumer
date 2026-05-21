import pytest


@pytest.mark.asyncio
async def test_health_no_auth(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "version" in data


@pytest.mark.asyncio
async def test_stats_requires_auth(client):
    resp = await client.get("/stats/60")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_stats_rejects_bad_key(client):
    resp = await client.get("/stats/60", headers={"x-api-key": "wrong"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_stats_accepts_valid_key(client):
    resp = await client.get("/stats/60", headers={"x-api-key": "test-key"})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_personas_requires_auth(client):
    resp = await client.post("/personas")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_personas_rejects_bad_key(client):
    resp = await client.post("/personas", headers={"x-api-key": "bad"})
    assert resp.status_code == 401
