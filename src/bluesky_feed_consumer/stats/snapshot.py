"""Background loop that periodically rotates windows and writes snapshots to Postgres."""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bluesky_feed_consumer.config import Settings
from bluesky_feed_consumer.models.stats import StatSnapshot
from bluesky_feed_consumer.stats.processor import StatsProcessor, WindowSnapshot

logger = logging.getLogger(__name__)


async def run_snapshot_loop(
    processor: StatsProcessor,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """Rotate windows at their configured interval and persist snapshots.

    The loop runs at the shortest window interval. Each iteration checks
    which windows are due for rotation.
    """
    min_interval = min(settings.window_sizes)
    # Track when each window was last rotated
    last_rotated: dict[int, float] = {
        size: asyncio.get_event_loop().time() for size in settings.window_sizes
    }

    while True:
        await asyncio.sleep(min_interval)
        now = asyncio.get_event_loop().time()

        for window_size in settings.window_sizes:
            elapsed = now - last_rotated[window_size]
            if elapsed >= window_size:
                snap = processor.rotate_window(window_size)
                if snap:
                    await _persist_snapshot(snap, processor, session_factory)
                last_rotated[window_size] = now


async def _persist_snapshot(
    snap: WindowSnapshot,
    processor: StatsProcessor,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Write a WindowSnapshot to the database as a StatSnapshot row."""
    # Read the *old* previous before promoting the new snapshot
    prev = processor.get_previous_snapshot(snap.window_seconds)
    processor.commit_previous(snap.window_seconds, snap)

    try:
        async with session_factory() as session:
            row = StatSnapshot(
                window_seconds=snap.window_seconds,
                window_start=snap.window_start,
                post_count=snap.post_count,
                user_count=snap.user_count,
                like_count=snap.like_count,
                repost_count=snap.repost_count,
                reply_count=snap.reply_count,
                prev_post_count=prev.post_count if prev else None,
                prev_user_count=prev.user_count if prev else None,
                prev_like_count=prev.like_count if prev else None,
                prev_repost_count=prev.repost_count if prev else None,
                prev_reply_count=prev.reply_count if prev else None,
                top_liked=snap.top_liked,
                top_reposted=snap.top_reposted,
                language_breakdown=snap.language_breakdown,
            )
            session.add(row)
            await session.commit()
            logger.info(
                "Persisted snapshot: window=%ds posts=%d users=%d",
                snap.window_seconds,
                snap.post_count,
                snap.user_count,
            )
    except Exception:
        logger.exception("Failed to persist snapshot for window=%d", snap.window_seconds)


async def flush_stats_to_db(
    processor: StatsProcessor,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Flush all in-flight window data to DB on shutdown. Called from lifespan."""
    logger.info("Flushing in-flight stats to database on shutdown...")
    for window_size in list(processor.windows.keys()):
        snap = processor.rotate_window(window_size)
        if snap and snap.post_count > 0:
            await _persist_snapshot(snap, processor, session_factory)
    logger.info("Stats flush complete.")
