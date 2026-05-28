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
        snap = proc.rotate_window(60)
        assert snap is not None
        # Caller must explicitly commit the snapshot as "previous"
        proc.commit_previous(60, snap)

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
        # Verify the ingested event is reflected in the metrics
        w60 = windows["60"]
        assert w60["metrics"]["post_count"] == 1
        assert w60["metrics"]["like_count"] == 0

    def test_deltas_computed_after_two_rotations(self) -> None:
        proc = StatsProcessor(_settings())

        # First window: 10 posts
        for _ in range(10):
            proc.ingest(_event("post"))
        snap1 = proc.rotate_window(60)
        assert snap1 is not None
        proc.commit_previous(60, snap1)

        # Second window: 15 posts
        for _ in range(15):
            proc.ingest(_event("post"))

        # Simulate being at the end of the 60 s window so extrapolation
        # scale ≈ 1 and the delta reflects actual vs previous counts.
        window = proc.windows[60]
        window.window_start = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=60)

        stats = proc.get_current_stats()
        windows = stats["windows"]
        assert isinstance(windows, dict)
        window_60 = windows["60"]
        assert isinstance(window_60, dict)
        deltas = window_60["deltas"]
        assert isinstance(deltas, dict)
        assert deltas["post_count"] == 0.5  # (15-10)/10

    def test_deltas_none_when_previous_zero(self) -> None:
        """Delta is None when previous window count was 0 (avoid division by zero)."""
        proc = StatsProcessor(_settings())

        # First window: 0 posts (empty), just rotate
        snap0 = proc.rotate_window(60)
        assert snap0 is not None
        proc.commit_previous(60, snap0)

        # Second window: some posts
        proc.ingest(_event("post"))

        stats = proc.get_current_stats()
        windows = stats["windows"]
        assert isinstance(windows, dict)
        window_60 = windows["60"]
        assert isinstance(window_60, dict)
        deltas = window_60["deltas"]
        assert isinstance(deltas, dict)
        assert deltas["post_count"] is None


class TestTopNHeap:
    def test_record_like_target_tracks_counts(self) -> None:
        acc = WindowAccumulator(60, top_n_limit=3, top_languages=3)
        evt = _event("like")

        acc.record_like_target("at://post/1", evt)
        acc.record_like_target("at://post/1", evt)
        acc.record_like_target("at://post/2", evt)

        snap = acc.snapshot()
        assert len(snap.top_liked) == 2
        # Highest count first
        assert snap.top_liked[0]["count"] == 2
        assert snap.top_liked[0]["uri"] == "at://post/1"

    def test_record_repost_target_tracks_counts(self) -> None:
        acc = WindowAccumulator(60, top_n_limit=3, top_languages=3)
        evt = _event("repost")

        acc.record_repost_target("at://post/a", evt)
        acc.record_repost_target("at://post/b", evt)
        acc.record_repost_target("at://post/b", evt)

        snap = acc.snapshot()
        assert len(snap.top_reposted) == 2
        assert snap.top_reposted[0]["uri"] == "at://post/b"
        assert snap.top_reposted[0]["count"] == 2

    def test_heap_caps_at_limit(self) -> None:
        """Heap never exceeds top_n_limit entries."""
        acc = WindowAccumulator(60, top_n_limit=3, top_languages=3)
        evt = _event("like")

        # Add 5 different URIs with increasing counts
        for i in range(5):
            for _ in range(i + 1):
                acc.record_like_target(f"at://post/{i}", evt)

        snap = acc.snapshot()
        assert len(snap.top_liked) == 3
        # Should keep the top 3 by count (counts: 5, 4, 3)
        counts = [item["count"] for item in snap.top_liked]
        assert counts == [5, 4, 3]

    def test_heap_replaces_lowest_when_higher_arrives(self) -> None:
        """A new item with higher count displaces the lowest in the heap."""
        acc = WindowAccumulator(60, top_n_limit=2, top_languages=3)
        evt = _event("like")

        # Two items with count 1
        acc.record_like_target("at://low1", evt)
        acc.record_like_target("at://low2", evt)

        # New item with count 5 (should displace one of the 1-count items)
        for _ in range(5):
            acc.record_like_target("at://high", evt)

        snap = acc.snapshot()
        assert len(snap.top_liked) == 2
        uris = {item["uri"] for item in snap.top_liked}
        assert "at://high" in uris
