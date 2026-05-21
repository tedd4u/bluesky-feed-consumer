"""Async WebSocket client for Bluesky Jetstream firehose."""

from __future__ import annotations

import asyncio
import logging
import random

import websockets
from websockets.exceptions import ConnectionClosed

from bluesky_feed_consumer.config import Settings
from bluesky_feed_consumer.ingestion.parser import FirehoseEvent, parse_event
from bluesky_feed_consumer.monitoring import get_metrics
from bluesky_feed_consumer.stats.processor import StatsProcessor

logger = logging.getLogger(__name__)


class FirehoseConsumer:
    """Connects to Bluesky Jetstream and dispatches parsed events to the stats processor.

    Reconnects with exponential backoff on connection failure.
    """

    def __init__(self, settings: Settings, processor: StatsProcessor) -> None:
        self.settings = settings
        self.processor = processor
        self._reconnect_delay = settings.reconnect_base_delay
        self._events_processed: int = 0
        self._connected: bool = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def events_processed(self) -> int:
        return self._events_processed

    async def run(self) -> None:
        """Main loop: connect, consume, reconnect on failure."""
        while True:
            try:
                await self._consume()
            except (ConnectionClosed, OSError) as exc:
                self._connected = False
                logger.warning(
                    "Firehose connection lost: %s. Reconnecting in %.1fs...",
                    exc,
                    self._reconnect_delay,
                )
                await self._backoff()
            except asyncio.CancelledError:
                logger.info("Firehose consumer cancelled, shutting down.")
                self._connected = False
                return
            except Exception:
                self._connected = False
                logger.exception(
                    "Unexpected error in firehose consumer. Reconnecting in %.1fs...",
                    self._reconnect_delay,
                )
                await self._backoff()

    async def _consume(self) -> None:
        """Connect and process messages until disconnected."""
        logger.info("Connecting to Jetstream: %s", self.settings.jetstream_url)

        async with websockets.connect(
            self.settings.jetstream_url,
            ping_interval=30,
            ping_timeout=10,
            close_timeout=5,
        ) as ws:
            self._connected = True
            self._reconnect_delay = self.settings.reconnect_base_delay
            logger.info("Connected to Jetstream firehose.")

            async for raw_msg in ws:
                event = parse_event(raw_msg)
                if event is not None:
                    self._handle_event(event)
                    self._events_processed += 1

    def _handle_event(self, event: FirehoseEvent) -> None:
        """Dispatch a parsed event to the stats processor."""
        self.processor.ingest(event)
        get_metrics().record_firehose_event()

    async def _backoff(self) -> None:
        """Sleep with jitter, then increase delay up to max."""
        jitter = random.uniform(0, self._reconnect_delay * 0.1)
        await asyncio.sleep(self._reconnect_delay + jitter)
        self._reconnect_delay = min(
            self._reconnect_delay * 2,
            self.settings.reconnect_max_delay,
        )
