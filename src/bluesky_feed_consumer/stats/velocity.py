"""Ring buffer of 2-second buckets tracking posting velocity over the last hour."""

from __future__ import annotations

from collections import deque

from bluesky_feed_consumer.config import Settings
from bluesky_feed_consumer.ingestion.parser import FirehoseEvent

# Event kinds that count toward posting velocity
_POST_KINDS = frozenset({"post", "reply", "quote"})


class VelocityTracker:
    """Tracks posts-per-second in fixed-size 2-second buckets.

    The SSE push loop calls ``rotate_bucket()`` every 2 seconds to finalize
    the current bucket and start a new one.
    """

    def __init__(self, settings: Settings) -> None:
        self.bucket_seconds = settings.velocity_bucket_seconds
        bucket_count = settings.velocity_history_seconds // self.bucket_seconds
        self.buckets: deque[int] = deque(maxlen=bucket_count)
        self.current_count: int = 0

    def record(self, event: FirehoseEvent) -> None:
        """Increment the current bucket if the event is a post/reply/quote."""
        if event.kind in _POST_KINDS:
            self.current_count += 1

    def rotate_bucket(self) -> float:
        """Finalize the current bucket and return posts/sec for it.

        Called every ``bucket_seconds`` by the SSE push loop.
        """
        posts_per_sec = self.current_count / self.bucket_seconds
        self.buckets.append(self.current_count)
        self.current_count = 0
        return posts_per_sec

    def get_current_rate(self) -> float:
        """Return the posts/sec rate of the most recently completed bucket."""
        if not self.buckets:
            return 0.0
        return self.buckets[-1] / self.bucket_seconds

    def get_history(self) -> list[float]:
        """Return posts/sec for each completed bucket, oldest first."""
        return [count / self.bucket_seconds for count in self.buckets]
