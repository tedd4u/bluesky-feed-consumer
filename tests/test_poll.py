"""Tests for the persona background poll loop with real DB (SQLite) and mocked fetcher."""

import datetime
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bluesky_feed_consumer.config import Settings
from bluesky_feed_consumer.models import Base
from bluesky_feed_consumer.models.chat import Persona, PersonaPost, PersonaStatus
from bluesky_feed_consumer.persona.fetcher import FetchedPost, FetchedProfile
from bluesky_feed_consumer.persona.poll import _poll_once, _process_persona


@pytest.fixture
async def poll_session_factory():
    """Provide a session factory with tables created for poll tests."""
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        echo=False,
        execution_options={"schema_translate_map": {"chat": None, "stats": None}},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


def _settings() -> Settings:
    return Settings(
        database_url="sqlite+aiosqlite://",
        max_history_posts=10,
        persona_poll_interval=1.0,
    )


def _mock_fetcher(posts_count: int = 3) -> AsyncMock:
    fetcher = AsyncMock()
    fetcher.fetch_profile.return_value = FetchedProfile(
        did="did:plc:abc123",
        handle="test.bsky.social",
        display_name="Test User",
        bio="I test things",
        avatar_url="https://avatar.example/test.jpg",
    )
    fetcher.fetch_posts.return_value = [
        FetchedPost(
            uri=f"at://did:plc:abc123/app.bsky.feed.post/{i}",
            post_type="post" if i % 2 == 0 else "reply",
            text=f"Post number {i}",
            parent_text="Parent" if i % 2 != 0 else None,
            quoted_ref=None,
            langs=["en"],
            posted_at=datetime.datetime(2024, 6, 1, 12, 0, 0, tzinfo=datetime.UTC),
            like_count=i,
            repost_count=0,
            reply_count=0,
        )
        for i in range(posts_count)
    ]
    return fetcher


@pytest.mark.asyncio
async def test_poll_once_processes_loading_persona(poll_session_factory):
    """Poll finds loading personas and processes them."""
    settings = _settings()
    fetcher = _mock_fetcher(posts_count=4)

    # Insert a loading persona
    async with poll_session_factory() as session:
        persona = Persona(handle="test.bsky.social", status=PersonaStatus.LOADING)
        session.add(persona)
        await session.commit()

    await _poll_once(fetcher, poll_session_factory, settings)

    # Verify it's now ready with profile data
    async with poll_session_factory() as session:
        result = await session.execute(select(Persona))
        persona = result.scalar_one()
        assert persona.status == PersonaStatus.READY
        assert persona.display_name == "Test User"
        assert persona.bio == "I test things"
        assert persona.did == "did:plc:abc123"
        assert persona.total_posts == 4
        assert persona.last_corpus_update is not None

    fetcher.fetch_profile.assert_called_once_with("test.bsky.social")
    fetcher.fetch_posts.assert_called_once()


@pytest.mark.asyncio
async def test_poll_once_skips_ready_personas(poll_session_factory):
    """Poll doesn't re-process already-ready personas."""
    settings = _settings()
    fetcher = _mock_fetcher()

    async with poll_session_factory() as session:
        persona = Persona(handle="ready.bsky.social", status=PersonaStatus.READY)
        session.add(persona)
        await session.commit()

    await _poll_once(fetcher, poll_session_factory, settings)

    fetcher.fetch_profile.assert_not_called()


@pytest.mark.asyncio
async def test_process_persona_sets_error_on_failure(poll_session_factory):
    """If fetching fails, persona status becomes ERROR with message."""
    settings = _settings()
    fetcher = AsyncMock()
    fetcher.fetch_profile.side_effect = Exception("Network timeout")

    async with poll_session_factory() as session:
        persona = Persona(handle="fail.bsky.social", status=PersonaStatus.LOADING)
        session.add(persona)
        await session.commit()
        persona_id = persona.id

    # Build a detached persona object for _process_persona
    async with poll_session_factory() as session:
        result = await session.execute(select(Persona).where(Persona.id == persona_id))
        persona = result.scalar_one()

    await _process_persona(persona, fetcher, poll_session_factory, settings)

    async with poll_session_factory() as session:
        result = await session.execute(select(Persona).where(Persona.id == persona_id))
        persona = result.scalar_one()
        assert persona.status == PersonaStatus.ERROR
        assert "Network timeout" in (persona.error_message or "")


@pytest.mark.asyncio
async def test_posts_persisted_to_db(poll_session_factory):
    """Fetched posts are saved as PersonaPost records."""
    settings = _settings()
    fetcher = _mock_fetcher(posts_count=5)

    async with poll_session_factory() as session:
        persona = Persona(handle="posts.bsky.social", status=PersonaStatus.LOADING)
        session.add(persona)
        await session.commit()

    await _poll_once(fetcher, poll_session_factory, settings)

    async with poll_session_factory() as session:
        result = await session.execute(select(PersonaPost))
        posts = result.scalars().all()
        assert len(posts) == 5
        # Verify a mix of post types
        post_types = {p.post_type for p in posts}
        assert "post" in post_types
        assert "reply" in post_types


@pytest.mark.asyncio
async def test_duplicate_posts_not_inserted(poll_session_factory):
    """Re-running poll doesn't duplicate posts (dedup by post_uri)."""
    settings = _settings()
    fetcher = _mock_fetcher(posts_count=3)

    async with poll_session_factory() as session:
        persona = Persona(handle="dedup.bsky.social", status=PersonaStatus.LOADING)
        session.add(persona)
        await session.commit()

    # Process once
    await _poll_once(fetcher, poll_session_factory, settings)

    # Reset to loading and process again with same posts
    async with poll_session_factory() as session:
        result = await session.execute(select(Persona))
        persona = result.scalar_one()
        persona.status = PersonaStatus.LOADING
        await session.commit()

    await _poll_once(fetcher, poll_session_factory, settings)

    # Should still only have 3 posts (not 6)
    async with poll_session_factory() as session:
        result = await session.execute(select(PersonaPost))
        posts = result.scalars().all()
        assert len(posts) == 3
