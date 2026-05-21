"""Tests for the in-memory stats processor and window accumulation."""

import datetime

from bluesky_feed_consumer.config import Settings
from bluesky_feed_consumer.ingestion.parser import FirehoseEvent
from bluesky_feed_consumer.stats.processor import StatsProcessor, WindowAccumulator


def _settings() -> Settings:
    return Settings(
        window_sizes=[60],
        top_n_limit=5,
        top_languages=3,
    )


def _event(
    kind: str = "post",
    did: str = "did:plc:user1",
    langs: list[str] | None = None,
) -> FirehoseEvent:
    return FirehoseEvent(
        kind=kind,  # type: ignore[arg-type]
        did=did,
        uri=f"at://{did}/app.bsky.feed.post/rkey",
        text="test post",
        langs=langs or [],
        timestamp=datetime.datetime.now(datetime.UTC),
    )


class TestWindowAccumulator:
    def test_count_posts(self) -> None:
        acc = WindowAccumulator(60, top_n_limit=5, top_languages=3)
        acc.record(_event("post"))
        acc.record(_event("post"))
        acc.record(_event("reply"))

        snap = acc.snapshot()
        assert snap.post_count == 3  # posts + replies + quotes all count
        assert snap.reply_count == 1

    def test_count_likes_and_reposts(self) -> None:
        acc = WindowAccumulator(60, top_n_limit=5, top_languages=3)
        acc.record(_event("like"))
        acc.record(_event("like"))
        acc.record(_event("repost"))

        snap = acc.snapshot()
        assert snap.like_count == 2
        assert snap.repost_count == 1
        assert snap.post_count == 0

    def test_unique_users(self) -> None:
        acc = WindowAccumulator(60, top_n_limit=5, top_languages=3)
        acc.record(_event("post", did="did:plc:a"))
        acc.record(_event("post", did="did:plc:a"))
        acc.record(_event("post", did="did:plc:b"))

        snap = acc.snapshot()
        assert snap.user_count == 2

    def test_likes_dont_count_users(self) -> None:
        acc = WindowAccumulator(60, top_n_limit=5, top_languages=3)
        acc.record(_event("like", did="did:plc:liker"))

        snap = acc.snapshot()
        assert snap.user_count == 0

    def test_language_breakdown(self) -> None:
        acc = WindowAccumulator(60, top_n_limit=5, top_languages=2)
        acc.record(_event("post", langs=["en"]))
        acc.record(_event("post", langs=["en"]))
        acc.record(_event("post", langs=["ja"]))
        acc.record(_event("post", langs=["pt"]))
        acc.record(_event("post"))  # no lang → "other"

        snap = acc.snapshot()
        assert snap.language_breakdown["en"] == 0.4  # 2/5
        assert snap.language_breakdown["ja"] == 0.2  # 1/5
        # pt and "other" should both be grouped as "other" (only top 2 + other)
        assert "other" in snap.language_breakdown

    def test_reset_clears_state(self) -> None:
        acc = WindowAccumulator(60, top_n_limit=5, top_languages=3)
        acc.record(_event("post"))
        acc.record(_event("like"))
        acc.reset()

        snap = acc.snapshot()
        assert snap.post_count == 0
        assert snap.like_count == 0
        assert snap.user_count == 0

    def test_empty_language_breakdown(self) -> None:
        acc = WindowAccumulator(60, top_n_limit=5, top_languages=3)
        snap = acc.snapshot()
        assert snap.language_breakdown == {}


class TestStatsProcessor:
    def test_ingest_updates_all_windows(self) -> None:
        settings = Settings(window_sizes=[60, 300])
        proc = StatsProcessor(settings)
        proc.ingest(_event("post"))

        s60 = proc.windows[60].snapshot()
        s300 = proc.windows[300].snapshot()
        assert s60.post_count == 1
        assert s300.post_count == 1

    def test_rotate_window_produces_snapshot(self) -> None:
        proc = StatsProcessor(_settings())
        proc.ingest(_event("post"))
        proc.ingest(_event("post"))

        snap = proc.rotate_window(60)
        assert snap is not None
        assert snap.post_count == 2
        assert snap.window_seconds == 60

        # After rotation, window should be reset
        current = proc.windows[60].snapshot()
        assert current.post_count == 0

    def test_rotate_stores_previous(self) -> None:
        proc = StatsProcessor(_settings())
        proc.ingest(_event("post"))
        proc.rotate_window(60)

        prev = proc.get_previous_snapshot(60)
        assert prev is not None
        assert prev.post_count == 1

    def test_rotate_invalid_window(self) -> None:
        proc = StatsProcessor(_settings())
        assert proc.rotate_window(999) is None

    def test_get_current_stats_shape(self) -> None:
        proc = StatsProcessor(_settings())
        proc.ingest(_event("post"))

        stats = proc.get_current_stats()
        assert "timestamp" in stats
        assert "windows" in stats
        assert "velocity" in stats
        windows = stats["windows"]
        assert isinstance(windows, dict)
        assert "60" in windows

    def test_deltas_computed_after_two_rotations(self) -> None:
        proc = StatsProcessor(_settings())

        # First window: 10 posts
        for _ in range(10):
            proc.ingest(_event("post"))
        proc.rotate_window(60)

        # Second window: 15 posts
        for _ in range(15):
            proc.ingest(_event("post"))

        stats = proc.get_current_stats()
        windows = stats["windows"]
        assert isinstance(windows, dict)
        window_60 = windows["60"]
        assert isinstance(window_60, dict)
        deltas = window_60["deltas"]
        assert isinstance(deltas, dict)
        assert deltas["post_count"] == 0.5  # (15-10)/10
