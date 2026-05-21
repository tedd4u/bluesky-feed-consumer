from fastapi import APIRouter

from bluesky_feed_consumer.api.deps import RequireAuth

router = APIRouter(prefix="/personas", tags=["personas"], dependencies=[RequireAuth])


@router.post("")
async def create_persona() -> dict[str, str]:
    return {"status": "not_implemented"}


@router.get("/{handle}/status")
async def get_persona_status(handle: str) -> dict[str, str]:
    return {"handle": handle, "status": "not_implemented"}


@router.post("/{handle}/chat")
async def post_chat_message(handle: str) -> dict[str, str]:
    return {"handle": handle, "status": "not_implemented"}


@router.get("/{handle}/chat")
async def get_chat_history(handle: str) -> dict[str, str]:
    return {"handle": handle, "status": "not_implemented"}


@router.delete("/{handle}/chat")
async def delete_chat_history(handle: str) -> dict[str, str]:
    return {"handle": handle, "status": "not_implemented"}
