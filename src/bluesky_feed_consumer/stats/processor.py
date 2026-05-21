"""In-memory rolling window aggregation engine for firehose stats."""

from __future__ import annotations

import datetime
import heapq
from dataclasses import dataclass

from bluesky_feed_consumer.config import Settings
from bluesky_feed_consumer.ingestion.parser import FirehoseEvent
from bluesky_feed_consumer.stats.velocity import VelocityTracker

# Event kinds that contribute text-post metrics (not likes/reposts)
_TEXT_KINDS = frozenset({"post", "reply", "quote"})


@dataclass
class TopItem:
    """A candidate for a top-N list, sortable by count."""

    uri: str
    did: str
    text: str
    count: int
    timestamp: datetime.datetime

    def __lt__(self, other: TopItem) -> bool:
        """Min-heap: lowest count is popped first."""
        return self.count < other.count


@dataclass
class WindowSnapshot:
    """Immutable snapshot of a completed window, ready for DB persistence."""

    window_seconds: int
    window_start: datetime.datetime
    post_count: int
    user_count: int
    like_count: int
    repost_count: int
    reply_count: int
    top_liked: list[dict[str, object]]
    top_reposted: list[dict[str, object]]
    language_breakdown: dict[str, float]


class WindowAccumulator:
    """Accumulates stats for a single time window.

    Tracks counts, unique users, top-N heaps, and language counters.
    """

    def __init__(self, window_seconds: int, top_n_limit: int, top_languages: int) -> None:
        self.window_seconds = window_seconds
        self.top_n_limit = top_n_limit
        self.top_languages = top_languages

        self.window_start = datetime.datetime.now(datetime.UTC)
        self.post_count = 0
        self.like_count = 0
        self.repost_count = 0
        self.reply_count = 0
        self.users: set[str] = set()

        # Min-heaps capped at top_n_limit
        self._liked_heap: list[TopItem] = []
        self._reposted_heap: list[TopItem] = []
        # Track running counts so we can update heap items
        self._like_counts: dict[str, int] = {}
        self._repost_counts: dict[str, int] = {}

        self._lang_counts: dict[str, int] = {}

    def record(self, event: FirehoseEvent) -> None:
        """Record a firehose event into this window's accumulators."""
        if event.kind == "like":
            self.like_count += 1
            return

        if event.kind == "repost":
            self.repost_count += 1
            return

        # post, reply, or quote
        self.users.add(event.did)
        self.post_count += 1

        if event.kind == "reply":
            self.reply_count += 1

        # Language tracking
        if event.langs:
            for lang in event.langs:
                self._lang_counts[lang] = self._lang_counts.get(lang, 0) + 1
        else:
            self._lang_counts["other"] = self._lang_counts.get("other", 0) + 1

    def record_like_target(self, target_uri: str, event: FirehoseEvent) -> None:
        """Track a like event for the top-liked heap.

        Called separately because the target post info may not be available
        from the firehose like event alone. For now, we track URI-level counts.
        """
        count = self._like_counts.get(target_uri, 0) + 1
        self._like_counts[target_uri] = count
        self._update_heap(
            self._liked_heap,
            TopItem(
                uri=target_uri, did="", text="", count=count,
                timestamp=event.timestamp,
            ),
        )

    def record_repost_target(self, target_uri: str, event: FirehoseEvent) -> None:
        """Track a repost for the top-reposted heap."""
        count = self._repost_counts.get(target_uri, 0) + 1
        self._repost_counts[target_uri] = count
        self._update_heap(
            self._reposted_heap,
            TopItem(
                uri=target_uri, did="", text="", count=count,
                timestamp=event.timestamp,
            ),
        )

    def _update_heap(self, heap: list[TopItem], item: TopItem) -> None:
        """Maintain a min-heap of size top_n_limit with highest-count items."""
        # Remove any existing entry for this URI so we replace with updated count
        heap[:] = [h for h in heap if h.uri != item.uri]
        if len(heap) < self.top_n_limit:
            heapq.heappush(heap, item)
        elif item.count > heap[0].count:
            heapq.heapreplace(heap, item)

    def snapshot(self) -> WindowSnapshot:
        """Produce an immutable snapshot of the current window state."""
        return WindowSnapshot(
            window_seconds=self.window_seconds,
            window_start=self.window_start,
            post_count=self.post_count,
            user_count=len(self.users),
            like_count=self.like_count,
            repost_count=self.repost_count,
            reply_count=self.reply_count,
            top_liked=self._heap_to_list(self._liked_heap),
            top_reposted=self._heap_to_list(self._reposted_heap),
            language_breakdown=self._compute_language_breakdown(),
        )

    def reset(self) -> None:
        """Reset all accumulators for a new window period."""
        self.window_start = datetime.datetime.now(datetime.UTC)
        self.post_count = 0
        self.like_count = 0
        self.repost_count = 0
        self.reply_count = 0
        self.users.clear()
        self._liked_heap.clear()
        self._reposted_heap.clear()
        self._like_counts.clear()
        self._repost_counts.clear()
        self._lang_counts.clear()

    def _heap_to_list(self, heap: list[TopItem]) -> list[dict[str, object]]:
        """Convert a min-heap into a sorted list (highest count first)."""
        items = sorted(heap, reverse=True, key=lambda x: x.count)
        return [
            {
                "uri": item.uri,
                "did": item.did,
                "text": item.text,
                "count": item.count,
                "timestamp": item.timestamp.isoformat(),
            }
            for item in items
        ]

    def _compute_language_breakdown(self) -> dict[str, float]:
        """Return top-L languages as fractions, with remainder grouped as 'other'."""
        total = sum(self._lang_counts.values())
        if total == 0:
            return {}

        sorted_langs = sorted(
            self._lang_counts.items(), key=lambda x: x[1], reverse=True
        )

        breakdown: dict[str, float] = {}
        other_count = 0

        for i, (lang, count) in enumerate(sorted_langs):
            if i < self.top_languages and lang != "other":
                breakdown[lang] = round(count / total, 4)
            else:
                other_count += count

        if other_count > 0:
            breakdown["other"] = round(other_count / total, 4)

        return breakdown


class StatsProcessor:
    """Central stats engine. Receives all firehose events and updates windows + velocity."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.velocity = VelocityTracker(settings)
        self.windows: dict[int, WindowAccumulator] = {
            size: WindowAccumulator(size, settings.top_n_limit, settings.top_languages)
            for size in settings.window_sizes
        }
        # Store previous snapshots for period-over-period delta calculation
        self._previous: dict[int, WindowSnapshot] = {}

    def ingest(self, event: FirehoseEvent) -> None:
        """Called for every firehose event. Updates all windows + velocity."""
        self.velocity.record(event)
        for window in self.windows.values():
            window.record(event)

    def rotate_window(self, window_seconds: int) -> WindowSnapshot | None:
        """Rotate a specific window: snapshot current state, reset, return snapshot."""
        window = self.windows.get(window_seconds)
        if window is None:
            return None

        snap = window.snapshot()
        self._previous[window_seconds] = snap
        window.reset()
        return snap

    def get_previous_snapshot(self, window_seconds: int) -> WindowSnapshot | None:
        """Return the previous snapshot for delta calculation."""
        return self._previous.get(window_seconds)

    def get_current_stats(self) -> dict[str, object]:
        """Return current in-memory stats for all windows. Used by SSE endpoint."""
        windows: dict[str, object] = {}
        for size, window in self.windows.items():
            snap = window.snapshot()
            prev = self._previous.get(size)
            windows[str(size)] = {
                "window_seconds": size,
                "window_start": snap.window_start.isoformat(),
                "metrics": {
                    "post_count": snap.post_count,
                    "user_count": snap.user_count,
                    "like_count": snap.like_count,
                    "repost_count": snap.repost_count,
                    "reply_count": snap.reply_count,
                },
                "deltas": _compute_deltas(snap, prev) if prev else None,
                "top_liked": snap.top_liked,
                "top_reposted": snap.top_reposted,
                "language_breakdown": snap.language_breakdown,
            }

        return {
            "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
            "windows": windows,
            "velocity": {
                "current": self.velocity.get_current_rate(),
                "history": self.velocity.get_history(),
            },
        }


def _compute_deltas(
    current: WindowSnapshot, previous: WindowSnapshot
) -> dict[str, float | None]:
    """Compute period-over-period percentage change."""
    result: dict[str, float | None] = {}
    for metric in ("post_count", "user_count", "like_count", "repost_count", "reply_count"):
        cur_val = getattr(current, metric)
        prev_val = getattr(previous, metric)
        if prev_val and prev_val > 0:
            result[metric] = round((cur_val - prev_val) / prev_val, 4)
        else:
            result[metric] = None
    return result
