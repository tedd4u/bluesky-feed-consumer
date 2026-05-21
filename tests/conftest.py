import pytest
from httpx import ASGITransport, AsyncClient

from bluesky_feed_consumer.app import create_app
from bluesky_feed_consumer.config import Settings, get_settings


@pytest.fixture
def settings() -> Settings:
    return Settings(
        database_url="sqlite+aiosqlite://",
        api_key="test-key",
    )


@pytest.fixture
def app(settings):
    application = create_app()
    application.dependency_overrides[get_settings] = lambda: settings
    return application


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
