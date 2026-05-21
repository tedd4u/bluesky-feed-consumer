"""Tests for the persona chat generator helpers."""

import datetime

from bluesky_feed_consumer.models.chat import ChatMessage, MessageRole
from bluesky_feed_consumer.persona.generator import _build_messages


class TestBuildMessages:
    def test_empty_history(self) -> None:
        """With no history, just the user message."""
        result = _build_messages([], "Hello!")
        assert len(result) == 1
        assert result[0] == {"role": "user", "content": "Hello!"}

    def test_preserves_history_order(self) -> None:
        """History messages appear in order, then the new user message last."""
        history = [
            ChatMessage(
                persona_id=1,
                role=MessageRole.USER,
                content="Hi there",
                created_at=datetime.datetime(2024, 1, 1, tzinfo=datetime.UTC),
            ),
            ChatMessage(
                persona_id=1,
                role=MessageRole.ASSISTANT,
                content="Hey! What's up?",
                created_at=datetime.datetime(2024, 1, 1, 0, 1, tzinfo=datetime.UTC),
            ),
        ]
        result = _build_messages(history, "Not much, you?")

        assert len(result) == 3
        assert result[0] == {"role": "user", "content": "Hi there"}
        assert result[1] == {"role": "assistant", "content": "Hey! What's up?"}
        assert result[2] == {"role": "user", "content": "Not much, you?"}

    def test_roles_mapped_correctly(self) -> None:
        """MessageRole enum values map to correct Claude API role strings."""
        history = [
            ChatMessage(
                persona_id=1,
                role=MessageRole.ASSISTANT,
                content="I'm a bot",
                created_at=datetime.datetime(2024, 1, 1, tzinfo=datetime.UTC),
            ),
        ]
        result = _build_messages(history, "Are you?")
        assert result[0]["role"] == "assistant"
        assert result[1]["role"] == "user"
