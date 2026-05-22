"""Tests for CORS middleware configuration."""

from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from bluesky_feed_consumer.app import create_app
from bluesky_feed_consumer.config import Settings, get_settings
from bluesky_feed_consumer.db import get_session
from bluesky_feed_consumer.stats.processor import StatsProcessor


def _make_settings(**overrides) -> Settings:
    defaults = dict(
        database_url="sqlite+aiosqlite://",
        service_api_key="test-key",
        jetstream_url="",
        anthropic_api_key="test-anthropic-key",
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _make_app(settings: Settings, db_session):
    """Create app with settings patched before middleware is configured."""
    with patch("bluesky_feed_consumer.app.get_settings", return_value=settings):
        application = create_app()
    application.dependency_overrides[get_settings] = lambda: settings
    application.dependency_overrides[get_session] = lambda: db_session
    application.state.processor = StatsProcessor(settings)
    application.state.settings = settings
    return application


@pytest.fixture
async def wildcard_client(db_session):
    settings = _make_settings(cors_origins=["*"])
    app = _make_app(settings, db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def restricted_client(db_session):
    settings = _make_settings(
        cors_origins=["https://app.example.com", "http://localhost:19006"]
    )
    app = _make_app(settings, db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# --- Wildcard origin tests ---


@pytest.mark.asyncio
async def test_preflight_returns_cors_headers(wildcard_client):
    """OPTIONS preflight request returns appropriate CORS headers."""
    resp = await wildcard_client.options(
        "/health",
        headers={
            "origin": "http://localhost:19006",
            "access-control-request-method": "GET",
        },
    )
    assert resp.status_code == 200
    # With allow_credentials=True, Starlette echoes the origin rather than "*"
    assert resp.headers["access-control-allow-origin"] == "http://localhost:19006"
    assert "GET" in resp.headers["access-control-allow-methods"]


@pytest.mark.asyncio
async def test_preflight_allows_custom_headers(wildcard_client):
    """Preflight allows x-api-key and content-type headers."""
    resp = await wildcard_client.options(
        "/stats/60",
        headers={
            "origin": "http://localhost:19006",
            "access-control-request-method": "GET",
            "access-control-request-headers": "x-api-key, content-type",
        },
    )
    assert resp.status_code == 200
    allowed = resp.headers.get("access-control-allow-headers", "").lower()
    assert "x-api-key" in allowed or "*" in allowed


@pytest.mark.asyncio
async def test_simple_get_includes_cors_header(wildcard_client):
    """A simple GET with Origin header gets access-control-allow-origin back."""
    resp = await wildcard_client.get(
        "/health",
        headers={"origin": "http://localhost:19006"},
    )
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "http://localhost:19006"


@pytest.mark.asyncio
async def test_any_origin_allowed_with_wildcard(wildcard_client):
    """Any origin is accepted when cors_origins=["*"]."""
    resp = await wildcard_client.get(
        "/health",
        headers={"origin": "https://random-site.example.org"},
    )
    assert resp.status_code == 200
    assert (
        resp.headers["access-control-allow-origin"]
        == "https://random-site.example.org"
    )


@pytest.mark.asyncio
async def test_post_with_origin_includes_cors_header(wildcard_client):
    """POST requests also get CORS headers."""
    resp = await wildcard_client.post(
        "/personas",
        headers={
            "origin": "http://localhost:19006",
            "x-api-key": "test-key",
            "content-type": "application/json",
        },
        json={"handle": "test.bsky.social"},
    )
    # 201 or 409 — either way CORS headers should be present
    assert resp.headers["access-control-allow-origin"] == "http://localhost:19006"


@pytest.mark.asyncio
async def test_no_origin_no_cors_headers(wildcard_client):
    """Requests without Origin header don't get CORS response headers."""
    resp = await wildcard_client.get("/health")
    assert resp.status_code == 200
    assert "access-control-allow-origin" not in resp.headers


@pytest.mark.asyncio
async def test_credentials_header_present(wildcard_client):
    """access-control-allow-credentials is set to true."""
    resp = await wildcard_client.get(
        "/health",
        headers={"origin": "http://localhost:19006"},
    )
    assert resp.headers["access-control-allow-credentials"] == "true"


# --- Restricted origin tests ---


@pytest.mark.asyncio
async def test_restricted_allows_configured_origin(restricted_client):
    """When origins are restricted, allowed origins get CORS headers."""
    resp = await restricted_client.get(
        "/health",
        headers={"origin": "https://app.example.com"},
    )
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "https://app.example.com"


@pytest.mark.asyncio
async def test_restricted_allows_localhost(restricted_client):
    """Localhost dev origin is allowed when configured."""
    resp = await restricted_client.get(
        "/health",
        headers={"origin": "http://localhost:19006"},
    )
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "http://localhost:19006"


@pytest.mark.asyncio
async def test_restricted_blocks_unknown_origin(restricted_client):
    """Origins not in the allow list don't get CORS allow-origin header."""
    resp = await restricted_client.get(
        "/health",
        headers={"origin": "https://evil.example.com"},
    )
    assert resp.status_code == 200
    # Starlette omits the header entirely for disallowed origins
    assert resp.headers.get("access-control-allow-origin") != "https://evil.example.com"


@pytest.mark.asyncio
async def test_restricted_preflight_blocks_unknown_origin(restricted_client):
    """Preflight from unlisted origin is rejected with 400."""
    resp = await restricted_client.options(
        "/stats/60",
        headers={
            "origin": "https://evil.example.com",
            "access-control-request-method": "GET",
        },
    )
    # Starlette returns 400 for disallowed preflight origins
    assert resp.status_code == 400
    assert resp.headers.get("access-control-allow-origin") != "https://evil.example.com"
