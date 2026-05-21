import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bluesky_feed_consumer.app import create_app
from bluesky_feed_consumer.config import Settings, get_settings
from bluesky_feed_consumer.db import get_session
from bluesky_feed_consumer.models import Base


@pytest.fixture
def settings() -> Settings:
    return Settings(
        database_url="sqlite+aiosqlite://",
        api_key="test-key",
        jetstream_url="",
        anthropic_api_key="test-anthropic-key",
    )


@pytest.fixture
async def db_session(settings):
    engine = create_async_engine(
        settings.database_url,
        echo=False,
        execution_options={"schema_translate_map": {"chat": None, "stats": None}},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture
def app(settings, db_session):
    application = create_app()
    application.dependency_overrides[get_settings] = lambda: settings
    application.dependency_overrides[get_session] = lambda: db_session
    return application


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
