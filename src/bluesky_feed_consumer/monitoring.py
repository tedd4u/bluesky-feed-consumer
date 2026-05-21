"""Cloud Monitoring metrics and structured logging setup."""

from __future__ import annotations

import json
import logging
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any


class _JsonFormatter(logging.Formatter):
    """Emit structured JSON logs compatible with Cloud Logging."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "severity": record.levelname,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "timestamp": self.formatTime(record, self.datefmt),
        }
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry)


def setup_logging(*, json_format: bool = True, level: str = "INFO") -> None:
    """Configure root logger with structured JSON output.

    Args:
        json_format: If True, emit JSON lines. If False, use standard format (for local dev).
        level: Log level name.
    """
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Remove existing handlers
    for handler in root.handlers[:]:
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    if json_format:
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
    root.addHandler(handler)

    # Quiet noisy libraries
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


class MetricsCollector:
    """Lightweight in-process metrics collector.

    Tracks counters and gauges that are periodically written to Cloud Monitoring
    via the background emitter, and also exposed on a /metrics endpoint for
    the uptime check dashboard.
    """

    def __init__(self) -> None:
        self.api_request_count: int = 0
        self.api_total_latency_ms: float = 0.0
        self.api_request_errors: int = 0
        self.firehose_events_total: int = 0
        self.sse_connections: int = 0
        self._last_emit_time: float = time.time()
        self._last_event_count: int = 0

    def record_request(self, latency_ms: float, *, error: bool = False) -> None:
        self.api_request_count += 1
        self.api_total_latency_ms += latency_ms
        if error:
            self.api_request_errors += 1

    def record_firehose_event(self) -> None:
        self.firehose_events_total += 1

    def sse_connect(self) -> None:
        self.sse_connections += 1

    def sse_disconnect(self) -> None:
        self.sse_connections = max(0, self.sse_connections - 1)

    def events_per_second(self) -> float:
        """Compute events/sec since last call."""
        now = time.time()
        elapsed = now - self._last_emit_time
        if elapsed <= 0:
            return 0.0
        count = self.firehose_events_total - self._last_event_count
        rate = count / elapsed
        self._last_emit_time = now
        self._last_event_count = self.firehose_events_total
        return rate

    def avg_latency_ms(self) -> float:
        if self.api_request_count == 0:
            return 0.0
        return self.api_total_latency_ms / self.api_request_count

    def snapshot(self) -> dict[str, float | int]:
        """Return current metrics as a dict (for /metrics endpoint)."""
        return {
            "firehose_events_total": self.firehose_events_total,
            "firehose_events_per_second": round(self.events_per_second(), 2),
            "api_request_count": self.api_request_count,
            "api_request_errors": self.api_request_errors,
            "api_avg_latency_ms": round(self.avg_latency_ms(), 2),
            "sse_connections": self.sse_connections,
        }


@asynccontextmanager
async def track_sse(metrics: MetricsCollector) -> AsyncIterator[None]:
    """Context manager to track SSE connection count."""
    metrics.sse_connect()
    try:
        yield
    finally:
        metrics.sse_disconnect()


# Module-level singleton — initialized in app lifespan
_metrics: MetricsCollector | None = None


def get_metrics() -> MetricsCollector:
    """Get the global metrics collector."""
    global _metrics  # noqa: PLW0603
    if _metrics is None:
        _metrics = MetricsCollector()
    return _metrics
