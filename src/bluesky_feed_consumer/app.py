import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from bluesky_feed_consumer.api.routes_personas import router as personas_router
from bluesky_feed_consumer.api.routes_sse import router as sse_router
from bluesky_feed_consumer.api.routes_stats import router as stats_router
from bluesky_feed_consumer.config import Settings, get_settings
from bluesky_feed_consumer.db import dispose_db, get_session_factory, init_db
from bluesky_feed_consumer.ingestion.consumer import FirehoseConsumer
from bluesky_feed_consumer.monitoring import get_metrics, setup_logging
from bluesky_feed_consumer.persona.fetcher import PersonaFetcher
from bluesky_feed_consumer.persona.poll import run_persona_poll
from bluesky_feed_consumer.stats.processor import StatsProcessor
from bluesky_feed_consumer.stats.snapshot import flush_stats_to_db, run_snapshot_loop

logger = logging.getLogger(__name__)


class _MetricsMiddleware(BaseHTTPMiddleware):
    """Track API request count and latency."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Skip metrics/health endpoints to avoid noise
        if request.url.path in ("/health", "/metrics"):
            return await call_next(request)
        start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - start) * 1000
        metrics = get_metrics()
        metrics.record_request(elapsed_ms, error=response.status_code >= 500)
        return response


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = get_settings()
    setup_logging(json_format=settings.json_logs)
    init_db(settings)
    session_factory = get_session_factory()

    # Build core services
    processor = StatsProcessor(settings)
    consumer = FirehoseConsumer(settings, processor)
    fetcher = PersonaFetcher(settings)

    # Store in app state for dependency injection in route handlers
    app.state.processor = processor
    app.state.consumer = consumer
    app.state.settings = settings
    app.state.session_factory = session_factory

    # Launch background tasks
    tasks: list[asyncio.Task[None]] = []
    if settings.jetstream_url:
        tasks.append(asyncio.create_task(consumer.run(), name="firehose-consumer"))
        tasks.append(
            asyncio.create_task(
                run_snapshot_loop(processor, session_factory, settings),
                name="snapshot-loop",
            )
        )

    tasks.append(
        asyncio.create_task(
            run_persona_poll(fetcher, session_factory, settings),
            name="persona-poll",
        )
    )
    logger.info("Background tasks started.")

    yield

    # Shutdown: flush stats, cancel background tasks
    if session_factory:
        await flush_stats_to_db(processor, session_factory)

    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)

    await fetcher.close()
    await dispose_db()
    logger.info("Shutdown complete.")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Bluesky Feed Consumer",
        description=(
            "Real-time Bluesky firehose consumer with rolling statistics "
            "and AI persona chat. Stats are computed in configurable time "
            "windows and streamed live via SSE. Persona chat lets you have "
            "conversations with AI impersonations of Bluesky accounts, "
            "powered by Claude."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(_MetricsMiddleware)
    app.include_router(stats_router)
    app.include_router(personas_router)
    app.include_router(sse_router)

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        """Service health check."""
        return {"status": "ok", "version": "0.1.0"}

    @app.get("/metrics", tags=["system"])
    async def metrics() -> dict[str, float | int]:
        """Operational metrics for monitoring dashboards."""
        return get_metrics().snapshot()

    return app
