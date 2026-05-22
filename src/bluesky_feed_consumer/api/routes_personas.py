"""Persona CRUD and chat API endpoints."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from bluesky_feed_consumer.api.deps import RequireAuth, SessionDep, SettingsDep
from bluesky_feed_consumer.models.chat import (
    ChatMessage,
    MessageRole,
    Persona,
    PersonaPost,
    PersonaStatus,
)
from bluesky_feed_consumer.persona.generator import PersonaGenerator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/personas", tags=["personas"], dependencies=[RequireAuth])


# --- Request/Response schemas ---


class CreatePersonaRequest(BaseModel):
    handle: str

    model_config = ConfigDict(json_schema_extra={"examples": [{"handle": "example.bsky.social"}]})


class PersonaResponse(BaseModel):
    handle: str
    display_name: str | None
    status: str
    post_count: int
    reply_count: int
    last_corpus_update: str | None
    avatar_url: str | None

    model_config = ConfigDict(from_attributes=True)


class PersonaStatusResponse(PersonaResponse):
    created_at: str
    error_message: str | None


class ChatMessageRequest(BaseModel):
    message: str

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"message": "Hey! What have you been up to lately?"}]}
    )


class ChatMessageResponse(BaseModel):
    role: str
    content: str
    created_at: str


class ChatHistoryResponse(BaseModel):
    handle: str
    messages: list[ChatMessageResponse]


# --- Endpoints ---


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_persona(
    body: CreatePersonaRequest,
    session: SessionDep,
) -> PersonaResponse:
    """Register a new persona for AI chat."""
    # Check for duplicate
    existing = await session.execute(select(Persona).where(Persona.handle == body.handle))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Persona @{body.handle} already exists",
        )

    persona = Persona(handle=body.handle, status=PersonaStatus.LOADING)
    session.add(persona)
    await session.commit()
    await session.refresh(persona)

    return _persona_to_response(persona)


@router.get("")
async def list_personas(session: SessionDep) -> list[PersonaResponse]:
    """List all registered personas."""
    result = await session.execute(select(Persona).order_by(Persona.created_at.desc()))
    personas = result.scalars().all()
    return [_persona_to_response(p) for p in personas]


@router.get("/{handle}/status")
async def get_persona_status(handle: str, session: SessionDep) -> PersonaStatusResponse:
    """Get detailed status for a persona."""
    persona = await _get_persona_or_404(handle, session)
    return PersonaStatusResponse(
        handle=persona.handle,
        display_name=persona.display_name,
        status=persona.status,
        post_count=persona.total_posts,
        reply_count=persona.total_replies,
        last_corpus_update=(
            persona.last_corpus_update.isoformat() if persona.last_corpus_update else None
        ),
        avatar_url=persona.avatar_url,
        created_at=persona.created_at.isoformat(),
        error_message=persona.error_message,
    )


@router.post("/{handle}/chat")
async def post_chat_message(
    handle: str,
    body: ChatMessageRequest,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
) -> EventSourceResponse:
    """Send a message and receive a streaming response via SSE."""
    persona = await _get_persona_or_404(handle, session)

    if persona.status != PersonaStatus.READY:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Persona @{handle} is not ready (status: {persona.status})",
        )

    # Load posts for context
    posts_result = await session.execute(
        select(PersonaPost)
        .where(PersonaPost.persona_id == persona.id)
        .order_by(PersonaPost.posted_at.desc())
    )
    posts: list[PersonaPost] = list(posts_result.scalars().all())

    # Load conversation history
    history_result = await session.execute(
        select(ChatMessage)
        .where(ChatMessage.persona_id == persona.id)
        .order_by(ChatMessage.created_at)
    )
    history: list[ChatMessage] = list(history_result.scalars().all())

    # Save user message
    user_msg = ChatMessage(
        persona_id=persona.id,
        role=MessageRole.USER,
        content=body.message,
    )
    session.add(user_msg)
    await session.commit()

    generator = PersonaGenerator(settings)

    async def event_stream() -> AsyncIterator[dict[str, str]]:
        full_response: list[str] = []
        try:
            async for token in generator.stream_response(persona, posts, history, body.message):
                full_response.append(token)
                yield {"event": "token", "data": f'{{"text": "{_escape_json(token)}"}}'}

            full_text = "".join(full_response)

            # Persist assistant response
            async with request.app.state.session_factory() as save_session:
                assistant_msg = ChatMessage(
                    persona_id=persona.id,
                    role=MessageRole.ASSISTANT,
                    content=full_text,
                )
                save_session.add(assistant_msg)
                await save_session.commit()

            yield {
                "event": "done",
                "data": f'{{"full_text": "{_escape_json(full_text)}", '
                f'"context_posts_used": {len(posts)}}}',
            }
        except Exception as exc:
            logger.exception("Error streaming persona response for @%s", handle)
            yield {"event": "error", "data": f'{{"error": "{_escape_json(str(exc))}"}}'}

    return EventSourceResponse(event_stream())


@router.get("/{handle}/chat")
async def get_chat_history(handle: str, session: SessionDep) -> ChatHistoryResponse:
    """Retrieve conversation history for a persona."""
    persona = await _get_persona_or_404(handle, session)

    result = await session.execute(
        select(ChatMessage)
        .where(ChatMessage.persona_id == persona.id)
        .order_by(ChatMessage.created_at)
    )
    messages = result.scalars().all()

    return ChatHistoryResponse(
        handle=handle,
        messages=[
            ChatMessageResponse(
                role=msg.role,
                content=msg.content,
                created_at=msg.created_at.isoformat(),
            )
            for msg in messages
        ],
    )


@router.delete("/{handle}/chat", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chat_history(handle: str, session: SessionDep) -> None:
    """Clear conversation history for a persona."""
    persona = await _get_persona_or_404(handle, session)

    result = await session.execute(select(ChatMessage).where(ChatMessage.persona_id == persona.id))
    messages = result.scalars().all()
    for msg in messages:
        await session.delete(msg)
    await session.commit()


# --- Helpers ---


async def _get_persona_or_404(handle: str, session: AsyncSession) -> Persona:
    result = await session.execute(select(Persona).where(Persona.handle == handle))
    persona = result.scalar_one_or_none()
    if persona is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Persona @{handle} not found",
        )
    return persona


def _persona_to_response(persona: Persona) -> PersonaResponse:
    return PersonaResponse(
        handle=persona.handle,
        display_name=persona.display_name,
        status=persona.status,
        post_count=persona.total_posts,
        reply_count=persona.total_replies,
        last_corpus_update=(
            persona.last_corpus_update.isoformat() if persona.last_corpus_update else None
        ),
        avatar_url=persona.avatar_url,
    )


def _escape_json(s: str) -> str:
    """Escape a string for embedding in JSON."""
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
