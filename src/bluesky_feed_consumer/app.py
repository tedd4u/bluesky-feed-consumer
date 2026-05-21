import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from bluesky_feed_consumer.api.routes_personas import router as personas_router
from bluesky_feed_consumer.api.routes_sse import router as sse_router
from bluesky_feed_consumer.api.routes_stats import router as stats_router
from bluesky_feed_consumer.config import Settings, get_settings
from bluesky_feed_consumer.db import dispose_db, get_session_factory, init_db
from bluesky_feed_consumer.ingestion.consumer import FirehoseConsumer
from bluesky_feed_consumer.stats.processor import StatsProcessor
from bluesky_feed_consumer.stats.snapshot import flush_stats_to_db, run_snapshot_loop

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = get_settings()
    init_db(settings)
    session_factory = get_session_factory()

    # Build core services
    processor = StatsProcessor(settings)
    consumer = FirehoseConsumer(settings, processor)

    # Store in app state for dependency injection in route handlers
    app.state.processor = processor
    app.state.consumer = consumer
    app.state.settings = settings

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
        logger.info("Background tasks started: firehose consumer, snapshot loop.")

    yield

    # Shutdown: flush stats, cancel background tasks
    if session_factory:
        await flush_stats_to_db(processor, session_factory)

    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)

    await dispose_db()
    logger.info("Shutdown complete.")


def create_app() -> FastAPI:
    app = FastAPI(title="Bluesky Feed Consumer", lifespan=lifespan)
    app.include_router(stats_router)
    app.include_router(personas_router)
    app.include_router(sse_router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
