from fastapi import APIRouter

from bluesky_feed_consumer.api.deps import RequireAuth

router = APIRouter(prefix="/stats", tags=["stats"], dependencies=[RequireAuth])


@router.get("/{window}")
async def get_stats(window: int) -> dict[str, object]:
    return {"window_seconds": window, "status": "not_implemented"}
