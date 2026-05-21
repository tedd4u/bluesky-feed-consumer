from fastapi import APIRouter

from bluesky_feed_consumer.api.deps import RequireAuth

router = APIRouter(prefix="/sse", tags=["sse"], dependencies=[RequireAuth])


@router.get("/stats")
async def stream_stats() -> dict[str, str]:
    return {"status": "not_implemented"}
