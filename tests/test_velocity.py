"""Tests for the velocity ring buffer tracker."""

import datetime

from bluesky_feed_consumer.config import Settings
from bluesky_feed_consumer.ingestion.parser import FirehoseEvent
from bluesky_feed_consumer.stats.velocity import VelocityTracker


def _settings(bucket_seconds: int = 2, history_seconds: int = 10) -> Settings:
    return Settings(
        velocity_bucket_seconds=bucket_seconds,
        velocity_history_seconds=history_seconds,
    )


def _event(kind: str = "post") -> FirehoseEvent:
    return FirehoseEvent(
        kind=kind,  # type: ignore[arg-type]
        did="did:plc:test",
        uri="at://test/post/1",
        timestamp=datetime.datetime.now(datetime.UTC),
    )


class TestVelocityTracker:
    def test_initial_state(self) -> None:
        tracker = VelocityTracker(_settings())
        assert tracker.get_current_rate() == 0.0
        assert tracker.get_history() == []

    def test_record_increments_current(self) -> None:
        tracker = VelocityTracker(_settings())
        tracker.record(_event("post"))
        tracker.record(_event("reply"))
        tracker.record(_event("quote"))
        assert tracker.current_count == 3

    def test_likes_and_reposts_not_counted(self) -> None:
        tracker = VelocityTracker(_settings())
        tracker.record(_event("like"))
        tracker.record(_event("repost"))
        assert tracker.current_count == 0

    def test_rotate_bucket_returns_rate(self) -> None:
        tracker = VelocityTracker(_settings(bucket_seconds=2))
        tracker.record(_event())
        tracker.record(_event())
        tracker.record(_event())
        tracker.record(_event())

        rate = tracker.rotate_bucket()
        assert rate == 2.0  # 4 events / 2 seconds
        assert tracker.current_count == 0

    def test_history_grows_on_rotation(self) -> None:
        tracker = VelocityTracker(_settings(bucket_seconds=2, history_seconds=10))
        # Max buckets = 10/2 = 5
        for i in range(3):
            for _ in range(i + 1):
                tracker.record(_event())
            tracker.rotate_bucket()

        history = tracker.get_history()
        assert len(history) == 3
        assert history == [0.5, 1.0, 1.5]  # 1/2, 2/2, 3/2

    def test_ring_buffer_caps_at_max(self) -> None:
        tracker = VelocityTracker(_settings(bucket_seconds=2, history_seconds=6))
        # Max buckets = 6/2 = 3
        for _ in range(5):
            tracker.record(_event())
            tracker.rotate_bucket()

        history = tracker.get_history()
        assert len(history) == 3  # capped

    def test_get_current_rate_from_last_bucket(self) -> None:
        tracker = VelocityTracker(_settings(bucket_seconds=2))
        tracker.record(_event())
        tracker.record(_event())
        tracker.rotate_bucket()

        assert tracker.get_current_rate() == 1.0  # 2 events / 2 sec
