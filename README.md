# Bluesky Feed Consumer

A real-time Bluesky firehose consumer that computes rolling statistics (post counts, top-N lists, language breakdown, posting velocity) exposed via REST API and SSE, plus an AI persona chat feature where users can have conversations with Claude-powered impersonations of Bluesky accounts. Built as a backend for new screens in a Bluesky app fork.

## Prerequisites

- Python 3.12+
- PostgreSQL 16+
- An [Anthropic API key](https://console.anthropic.com/) (for persona chat)

## Setup

Clone the repo and create a virtualenv:

```bash
git clone git@github.com:tedd4u/bluesky-feed-consumer.git
cd bluesky-feed-consumer
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Or with [uv](https://docs.astral.sh/uv/):

```bash
uv sync --all-extras
source .venv/bin/activate
```

Copy the example env file and fill in your values:

```bash
cp .env.example .env
# Edit .env with your database URL, API keys, etc.
```

## Database

Create the database and run migrations:

```bash
createdb bsky
alembic upgrade head
```

## Running

Full server (firehose consumer + API):

```bash
bsky-server
```

API only (no firehose, useful for local dev):

```bash
bsky-api-only
```

The API runs at `http://localhost:8000` by default. All endpoints require an `X-API-Key` header matching your configured `BSKY_API_KEY`.

## Development

Run all checks (lint + type check + tests):

```bash
make check
```

Individual targets:

```bash
make lint       # ruff
make typecheck  # mypy (strict)
make test       # pytest
make fmt        # auto-format + auto-fix
```

## Configuration

All config is via environment variables (prefixed `BSKY_`) or `.env` file. See `.env.example` for available options and `src/bluesky_feed_consumer/config.py` for defaults.

## Project Structure

```
src/bluesky_feed_consumer/
    app.py              # FastAPI app factory, lifespan hooks
    config.py           # Pydantic Settings
    cli.py              # CLI entrypoints
    db.py               # SQLAlchemy async engine + session
    models/             # ORM models (stats + chat schemas)
    ingestion/          # Jetstream WebSocket consumer + event parser
    stats/              # Rolling window aggregation, velocity tracker, snapshot persistence
    persona/            # Corpus management, context selection, Claude API chat (WIP)
    api/                # REST endpoints + auth middleware
```
