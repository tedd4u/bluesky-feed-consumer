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

The API runs at `http://localhost:8000` by default. All endpoints require an `X-Api-Key` header matching your configured `BSKY_SERVICE_API_KEY`.

## Development

Run all checks (lint + type check + tests):

```bash
make check
```

Individual targets:

```bash
make lint       # ruff
make typecheck  # mypy (strict)
make test       # pytest with line + branch coverage report
make fmt        # auto-format + auto-fix
```

`make test` runs with [pytest-cov](https://pytest-cov.readthedocs.io/) and prints a coverage table with missing lines after each run. No additional setup needed.

## GCP Deployment

The `infra/` directory contains shell scripts to provision and deploy the full environment on GCP. Two local config files (both gitignored) drive the scripts:

| File | Purpose |
|------|---------|
| `.env` | Application secrets + runtime config (API keys, database URL) |
| `infra/.env.infra` | GCP infrastructure config (project ID, region, billing, DB password) |

Copy the example and fill in your values:

```bash
cp infra/.env.infra.example infra/.env.infra
```

### Spin up from scratch

```bash
cd infra
./create-project.sh   # Create GCP project, link billing, enable APIs
./setup.sh            # Secret Manager, Cloud SQL, Compute Engine, firewall, DNS
./deploy.sh           # Pull code on CE, write .env from secrets, run migrations, start service
```

### Nuke and recreate

```bash
cd infra
./teardown.sh                              # Deletes the entire GCP project (confirms interactively)
./create-project.sh && ./setup.sh && ./deploy.sh   # Recreate from zero
```

### Deploy updates

```bash
cd infra
./deploy.sh           # Pulls latest code, syncs deps, re-writes .env, runs migrations, restarts
```

## API Exploration (Bruno)

An [OpenAPI 3.1 spec](docs/openapi.json) is included for importing into API clients. To set up [Bruno](https://www.usebruno.com/):

1. **Import**: Open Bruno → Import Collection → **OpenAPI V3** → select `docs/openapi.json`
2. **Environment**: Create a Bruno environment with two variables:
   - `baseUrl` — e.g. `http://localhost:8000` (or your deployed URL)
   - `apiKey` — your `BSKY_SERVICE_API_KEY` value
3. **Collection headers**: Add `X-Api-Key: {{apiKey}}` as a collection-level header so it applies to every request

The live service also exposes Swagger UI at `/docs` and the raw spec at `/openapi.json`.

To refresh the spec after endpoint changes:

```bash
curl -s http://localhost:8000/openapi.json | python3 -m json.tool > docs/openapi.json
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
    persona/            # Corpus management, context selection, Claude API chat
    api/                # REST endpoints + auth middleware
```
