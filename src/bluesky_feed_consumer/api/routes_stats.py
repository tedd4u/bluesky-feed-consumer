"""Stats API endpoints: windowed metrics and top-N lists."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, status

from bluesky_feed_consumer.api.deps import RequireAuth
from bluesky_feed_consumer.stats.processor import StatsProcessor

router = APIRouter(prefix="/stats", tags=["stats"], dependencies=[RequireAuth])


def _get_processor(request: Request) -> StatsProcessor:
    processor: StatsProcessor | None = getattr(request.app.state, "processor", None)
    if processor is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Stats processor not available",
        )
    return processor


@router.get("/{window}")
async def get_stats(
    window: int,
    request: Request,
    top_n: int = Query(
        default=10, ge=1, le=50, description="Number of top items to return (max 50)"
    ),
) -> dict[str, object]:
    """Return current metrics for a specific time window.

    Window is specified in seconds (e.g., 60 for 1-minute, 300 for 5-minute).
    Returns post/like/repost counts, top-N lists, and language breakdown.
    """
    processor = _get_processor(request)

    if window not in processor.windows:
        valid = sorted(processor.windows.keys())
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid window {window}. Valid: {valid}",
        )

    acc = processor.windows[window]
    snap = acc.snapshot()
    prev = processor.get_previous_snapshot(window)

    from bluesky_feed_consumer.stats.processor import _compute_deltas

    return {
        "window_seconds": window,
        "window_start": snap.window_start.isoformat(),
        "metrics": {
            "post_count": snap.post_count,
            "user_count": snap.user_count,
            "like_count": snap.like_count,
            "repost_count": snap.repost_count,
            "reply_count": snap.reply_count,
        },
        "deltas": _compute_deltas(snap, prev) if prev else None,
        "top_liked": snap.top_liked[:top_n],
        "top_reposted": snap.top_reposted[:top_n],
        "language_breakdown": snap.language_breakdown,
    }
