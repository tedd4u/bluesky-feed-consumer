from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from bluesky_feed_consumer.config import Settings

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def init_db(settings: Settings) -> None:
    global _engine, _session_factory
    _engine = create_async_engine(settings.database_url, echo=False)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)


async def dispose_db() -> None:
    global _engine, _session_factory
    if _engine:
        await _engine.dispose()
    _engine = None
    _session_factory = None


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the session factory. Must be called after init_db()."""
    assert _session_factory is not None, "Database not initialized. Call init_db() first."
    return _session_factory


async def get_session() -> AsyncIterator[AsyncSession]:
    assert _session_factory is not None, "Database not initialized. Call init_db() first."
    async with _session_factory() as session:
        yield session
