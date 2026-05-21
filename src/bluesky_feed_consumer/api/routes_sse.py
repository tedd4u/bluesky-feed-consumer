"""SSE endpoints for live-streaming stats to clients."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from bluesky_feed_consumer.api.deps import RequireAuth
from bluesky_feed_consumer.stats.processor import StatsProcessor

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sse", tags=["sse"], dependencies=[RequireAuth])


@router.get("/stats")
async def stream_stats(request: Request) -> EventSourceResponse:
    """Live stats stream via SSE.

    Sends a full snapshot on connect, then pushes updates every 2 seconds.
    Each update rotates the velocity bucket and includes current window stats.
    """
    processor: StatsProcessor = request.app.state.processor
    push_interval: float = request.app.state.settings.sse_push_interval

    async def event_stream() -> AsyncIterator[dict[str, str]]:
        # Initial snapshot with full velocity history
        stats = processor.get_current_stats()
        yield {
            "event": "snapshot",
            "data": json.dumps(stats),
        }

        # Subsequent updates every push_interval seconds
        while True:
            await asyncio.sleep(push_interval)

            # Check if client disconnected
            if await request.is_disconnected():
                break

            # Rotate velocity bucket (produces new rate data point)
            processor.velocity.rotate_bucket()

            stats = processor.get_current_stats()
            yield {
                "event": "update",
                "data": json.dumps(stats),
            }

    return EventSourceResponse(event_stream())
