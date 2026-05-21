"""Tests for the firehose WebSocket consumer with mocked websockets."""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from bluesky_feed_consumer.config import Settings
from bluesky_feed_consumer.ingestion.consumer import FirehoseConsumer
from bluesky_feed_consumer.stats.processor import StatsProcessor


def _settings() -> Settings:
    return Settings(
        jetstream_url="wss://fake.jetstream/subscribe",
        reconnect_base_delay=0.01,
        reconnect_max_delay=0.05,
    )


def _post_msg(text: str = "Hello") -> str:
    return json.dumps(
        {
            "did": "did:plc:test",
            "time_us": 1716220800_000_000,
            "kind": "commit",
            "commit": {
                "rev": "r1",
                "operation": "create",
                "collection": "app.bsky.feed.post",
                "rkey": "abc",
                "record": {"text": text, "langs": ["en"]},
            },
        }
    )


class TestFirehoseConsumer:
    def test_initial_state(self) -> None:
        settings = _settings()
        processor = StatsProcessor(settings)
        consumer = FirehoseConsumer(settings, processor)

        assert consumer.is_connected is False
        assert consumer.events_processed == 0

    @pytest.mark.asyncio
    async def test_processes_messages(self) -> None:
        """Consumer parses messages and dispatches to processor."""
        settings = _settings()
        processor = StatsProcessor(settings)
        consumer = FirehoseConsumer(settings, processor)

        messages = [_post_msg("msg1"), _post_msg("msg2"), "not json"]

        # Mock websockets.connect to yield our messages then disconnect
        mock_ws = AsyncMock()
        mock_ws.__aiter__.return_value = messages
        mock_ws.__aenter__.return_value = mock_ws
        mock_ws.__aexit__.return_value = False

        with patch("bluesky_feed_consumer.ingestion.consumer.websockets.connect") as mock_connect:
            mock_connect.return_value = mock_ws

            # Run _consume directly (avoids the reconnect loop)
            await consumer._consume()

        assert consumer.events_processed == 2
        assert consumer.is_connected is True
        # Processor should have received 2 posts
        snap = processor.windows[60].snapshot()
        assert snap.post_count == 2

    @pytest.mark.asyncio
    async def test_reconnects_on_connection_error(self) -> None:
        """Consumer retries after OSError."""
        settings = _settings()
        processor = StatsProcessor(settings)
        consumer = FirehoseConsumer(settings, processor)

        call_count = 0

        async def fake_consume() -> None:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise OSError("Connection refused")
            # Third call: cancel to stop the loop
            raise asyncio.CancelledError

        consumer._consume = fake_consume  # type: ignore[assignment]

        await consumer.run()
        assert call_count == 3
        assert consumer.is_connected is False

    @pytest.mark.asyncio
    async def test_backoff_increases(self) -> None:
        """Reconnect delay doubles up to max."""
        settings = _settings()
        processor = StatsProcessor(settings)
        consumer = FirehoseConsumer(settings, processor)

        initial_delay = consumer._reconnect_delay
        sleep_path = "bluesky_feed_consumer.ingestion.consumer.asyncio.sleep"
        with patch(sleep_path, new_callable=AsyncMock):
            await consumer._backoff()

        assert consumer._reconnect_delay == min(initial_delay * 2, settings.reconnect_max_delay)

    @pytest.mark.asyncio
    async def test_cancelled_error_exits_cleanly(self) -> None:
        """CancelledError causes clean shutdown, not reconnect."""
        settings = _settings()
        processor = StatsProcessor(settings)
        consumer = FirehoseConsumer(settings, processor)

        async def raise_cancelled() -> None:
            raise asyncio.CancelledError

        consumer._consume = raise_cancelled  # type: ignore[assignment]

        # Should return without retrying
        await consumer.run()
        assert consumer.is_connected is False
