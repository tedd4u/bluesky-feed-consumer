"""Claude API integration for persona chat streaming."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

import anthropic

from bluesky_feed_consumer.config import Settings
from bluesky_feed_consumer.models.chat import ChatMessage, Persona, PersonaPost
from bluesky_feed_consumer.persona.context import ContextSelector

logger = logging.getLogger(__name__)


class PersonaGenerator:
    """Generates streaming persona chat responses via the Claude API."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.context_selector = ContextSelector(settings)
        self.client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    async def stream_response(
        self,
        persona: Persona,
        posts: list[PersonaPost],
        conversation_history: list[ChatMessage],
        user_message: str,
    ) -> AsyncIterator[str]:
        """Build prompt from persona context, stream Claude response tokens.

        Yields individual text chunks as they arrive from the API.
        """
        selected_posts = self.context_selector.select(posts)
        system_prompt = self.context_selector.format_for_prompt(persona, selected_posts)
        messages = _build_messages(conversation_history, user_message)

        logger.info(
            "Streaming response for @%s (context: %d posts, history: %d msgs)",
            persona.handle,
            len(selected_posts),
            len(conversation_history),
        )

        async with self.client.messages.stream(
            model=self.settings.claude_model,
            system=system_prompt,
            messages=messages,
            max_tokens=1024,
        ) as stream:
            async for text in stream.text_stream:
                yield text


def _build_messages(
    history: list[ChatMessage], user_message: str
) -> list[anthropic.types.MessageParam]:
    """Convert conversation history + new message into Claude API format."""
    messages: list[anthropic.types.MessageParam] = []
    for msg in history:
        messages.append({"role": msg.role, "content": msg.content})  # type: ignore[typeddict-item]
    messages.append({"role": "user", "content": user_message})
    return messages
