import os

import uvicorn

from bluesky_feed_consumer.config import get_settings


def run_server() -> None:
    """Run the full server: firehose consumer + API."""
    settings = get_settings()
    uvicorn.run(
        "bluesky_feed_consumer.app:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        log_level="info",
    )


def run_api_only() -> None:
    """Run only the API server (no firehose consumer).

    Disables the firehose by clearing the Jetstream URL so the lifespan
    skips launching background tasks.
    """
    os.environ["BSKY_JETSTREAM_URL"] = ""
    settings = get_settings()
    uvicorn.run(
        "bluesky_feed_consumer.app:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        log_level="info",
    )
