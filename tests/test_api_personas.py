"""Tests for persona CRUD API endpoints."""

import pytest

AUTH = {"x-api-key": "test-key"}


@pytest.mark.asyncio
async def test_create_persona(client):
    resp = await client.post("/personas", json={"handle": "test.bsky.social"}, headers=AUTH)
    assert resp.status_code == 201
    data = resp.json()
    assert data["handle"] == "test.bsky.social"
    assert data["status"] == "loading"
    assert data["post_count"] == 0


@pytest.mark.asyncio
async def test_create_duplicate_persona(client):
    await client.post("/personas", json={"handle": "dupe.bsky.social"}, headers=AUTH)
    resp = await client.post("/personas", json={"handle": "dupe.bsky.social"}, headers=AUTH)
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_list_personas(client):
    await client.post("/personas", json={"handle": "a.bsky.social"}, headers=AUTH)
    await client.post("/personas", json={"handle": "b.bsky.social"}, headers=AUTH)

    resp = await client.get("/personas", headers=AUTH)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 2


@pytest.mark.asyncio
async def test_get_persona_status(client):
    await client.post("/personas", json={"handle": "status.bsky.social"}, headers=AUTH)
    resp = await client.get("/personas/status.bsky.social/status", headers=AUTH)
    assert resp.status_code == 200
    data = resp.json()
    assert data["handle"] == "status.bsky.social"
    assert data["status"] == "loading"
    assert "created_at" in data


@pytest.mark.asyncio
async def test_get_nonexistent_persona(client):
    resp = await client.get("/personas/nobody.bsky.social/status", headers=AUTH)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_chat_with_non_ready_persona(client):
    await client.post("/personas", json={"handle": "notready.bsky.social"}, headers=AUTH)
    resp = await client.post(
        "/personas/notready.bsky.social/chat",
        json={"message": "Hello"},
        headers=AUTH,
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_get_empty_chat_history(client):
    await client.post("/personas", json={"handle": "hist.bsky.social"}, headers=AUTH)
    resp = await client.get("/personas/hist.bsky.social/chat", headers=AUTH)
    assert resp.status_code == 200
    data = resp.json()
    assert data["handle"] == "hist.bsky.social"
    assert data["messages"] == []


@pytest.mark.asyncio
async def test_delete_chat_history(client):
    await client.post("/personas", json={"handle": "del.bsky.social"}, headers=AUTH)
    resp = await client.delete("/personas/del.bsky.social/chat", headers=AUTH)
    assert resp.status_code == 204
