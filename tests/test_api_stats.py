"""Tests for the stats API endpoints."""

import pytest

from bluesky_feed_consumer.stats.processor import StatsProcessor

AUTH = {"x-api-key": "test-key"}


@pytest.fixture
def processor(app, settings):
    """Wire a real StatsProcessor into app state."""
    proc = StatsProcessor(settings)
    app.state.processor = proc
    app.state.settings = settings
    return proc


@pytest.mark.asyncio
async def test_get_stats_empty_window(client, processor):
    resp = await client.get("/stats/60", headers=AUTH)
    assert resp.status_code == 200
    data = resp.json()
    assert data["window_seconds"] == 60
    assert data["metrics"]["post_count"] == 0
    assert data["deltas"] is None
    assert data["top_liked"] == []
    assert data["language_breakdown"] == {}


@pytest.mark.asyncio
async def test_get_stats_with_data(client, processor):
    from tests.test_stats_processor import _event

    for _ in range(5):
        processor.ingest(_event("post", langs=["en"]))
    processor.ingest(_event("like"))
    processor.ingest(_event("reply"))

    resp = await client.get("/stats/60", headers=AUTH)
    assert resp.status_code == 200
    data = resp.json()
    assert data["metrics"]["post_count"] == 6  # 5 posts + 1 reply
    assert data["metrics"]["like_count"] == 1
    assert data["metrics"]["reply_count"] == 1
    assert data["language_breakdown"]["en"] > 0


@pytest.mark.asyncio
async def test_get_stats_with_deltas(client, processor):
    from tests.test_stats_processor import _event

    # First window: 10 posts
    for _ in range(10):
        processor.ingest(_event("post"))
    snap1 = processor.rotate_window(60)
    assert snap1 is not None
    processor.commit_previous(60, snap1)

    # Second window: 5 posts
    for _ in range(5):
        processor.ingest(_event("post"))

    resp = await client.get("/stats/60", headers=AUTH)
    data = resp.json()
    assert data["deltas"] is not None
    assert data["deltas"]["post_count"] == -0.5  # (5-10)/10


@pytest.mark.asyncio
async def test_get_stats_invalid_window(client, processor):
    resp = await client.get("/stats/999", headers=AUTH)
    assert resp.status_code == 400
    assert "Invalid window" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_get_stats_top_n_param(client, processor):
    from tests.test_stats_processor import _event

    # Add likes to create top_liked entries
    evt = _event("like")
    acc = processor.windows[60]
    for i in range(10):
        for _ in range(i + 1):
            acc.record_like_target(f"at://post/{i}", evt)

    resp = await client.get("/stats/60?top_n=3", headers=AUTH)
    data = resp.json()
    assert len(data["top_liked"]) == 3


@pytest.mark.asyncio
async def test_get_stats_top_n_capped_at_50(client, processor):
    resp = await client.get("/stats/60?top_n=100", headers=AUTH)
    assert resp.status_code == 422  # validation error from Query(le=50)


@pytest.mark.asyncio
async def test_get_stats_requires_auth(client):
    resp = await client.get("/stats/60")
    assert resp.status_code == 422  # missing header
