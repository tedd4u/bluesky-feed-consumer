# Engineering Spec: Bluesky Feed Consumer

Based on [requirements.md](requirements.md). See [wireframes.html](wireframes.html) for UI reference.

## Table of Contents

1. [System Architecture](#1-system-architecture)
2. [Technology Stack](#2-technology-stack)
3. [Project Structure](#3-project-structure)
4. [Configuration](#4-configuration)
5. [Database Schema](#5-database-schema)
6. [API Contracts](#6-api-contracts)
7. [Module Design](#7-module-design)
8. [Application Lifecycle](#8-application-lifecycle)
9. [SSE Implementation](#9-sse-implementation)
10. [Firehose Event Processing](#10-firehose-event-processing)
11. [Deployment](#11-deployment)
12. [Testing Strategy](#12-testing-strategy)
13. [Error Handling](#13-error-handling)
14. [Development Environment](#14-development-environment)
15. [Implementation Order](#15-implementation-order)

---

## 1. System Architecture

Single Python process running on GCP Compute Engine (e2-small, 0.5 vCPU, 2GB RAM).

```
┌─────────────────────────────────────────────────────────┐
│                  Compute Engine (e2-small)              │
│                                                         │
│  ┌──────────────┐    ┌──────────────┐                   │
│  │   Firehose   │───▶│    Stats     │                   │
│  │   Consumer   │    │  Processor   │                   │
│  │  (WebSocket) │    │ (in-memory)  │                   │
│  └──────┬───────┘    └──────┬───────┘                   │
│         │                   │                           │
│         │  ┌────────────────┘                           │
│         │  │                                            │
│         ▼  ▼                                            │
│  ┌──────────────┐    ┌──────────────┐                   │
│  │   FastAPI    │    │   Persona    │                   │
│  │   Server     │◀──▶│   Module     │                   │
│  │  (REST + SSE)│    │ (context +   │                   │
│  └──────┬───────┘    │  Claude API) │                   │
│         │            └──────────────┘                   │
└─────────┼───────────────────────────────────────────────┘
          │
          ▼
┌──────────────────┐         ┌──────────────────┐
│   Cloud SQL      │         │   Claude API     │
│   (PostgreSQL)   │         │   (Anthropic)    │
└──────────────────┘         └──────────────────┘
```

**Key design choices**:
- Everything runs in a single async Python process. The firehose consumer, stats processor, and FastAPI server share memory.
- Live stats are read directly from in-memory data structures — no DB round-trip. Postgres is for persistence only.
- Standard CPython with GIL (no free-threading). Async IO via asyncio for concurrent WebSocket + HTTP serving.
- Python 3.12+ (not 3.14 — avoid t-strings and other features that are too new for framework compatibility).

---

## 2. Technology Stack

| Component | Technology | Version |
|-----------|-----------|---------|
| Language | Python | 3.12+ |
| Web Framework | FastAPI | 0.115+ |
| ASGI Server | Uvicorn | 0.30+ |
| SSE | sse-starlette | 2.0+ |
| ORM / DB | SQLAlchemy (async) + asyncpg | 2.0+ |
| Migrations | Alembic | 1.13+ |
| Config | Pydantic Settings | 2.0+ |
| HTTP Client | httpx (async) | 0.27+ |
| WebSocket Client | websockets | 12.0+ |
| AI | anthropic (Python SDK) | 0.40+ |
| Testing | pytest + pytest-asyncio + pytest-cov + httpx | |
| Linting | ruff | |
| Type Checking | mypy (strict) | |
| Package Management | uv | |

---

## 3. Project Structure

```
bluesky-feed-consumer/
├── pyproject.toml
├── alembic.ini
├── alembic/
│   └── versions/
├── src/
│   └── bluesky_feed_consumer/
│       ├── __init__.py
│       ├── app.py                 # FastAPI app factory, lifespan hooks
│       ├── config.py              # Pydantic Settings
│       ├── db.py                  # SQLAlchemy async engine + session
│       ├── models/
│       │   ├── __init__.py
│       │   ├── stats.py           # StatSnapshot ORM model
│       │   ├── persona.py         # Persona, PersonaPost models
│       │   └── chat.py            # ChatMessage model
│       ├── ingestion/
│       │   ├── __init__.py
│       │   ├── consumer.py        # Jetstream WebSocket consumer
│       │   └── parser.py          # Event classification + extraction
│       ├── stats/
│       │   ├── __init__.py
│       │   ├── processor.py       # Rolling window aggregation engine
│       │   ├── velocity.py        # 5-second bucket ring buffer
│       │   └── snapshot.py        # Periodic Postgres writer
│       ├── persona/
│       │   ├── __init__.py
│       │   ├── fetcher.py         # AT Protocol history fetcher
│       │   ├── context.py         # Context selection algorithm
│       │   └── generator.py       # Claude API prompt + streaming
│       ├── api/
│       │   ├── __init__.py
│       │   ├── deps.py            # Shared dependencies (auth, db session)
│       │   ├── stats.py           # GET /stats/{window}, GET /stats/stream
│       │   ├── personas.py        # POST /personas, GET /personas, GET /personas/{handle}/status
│       │   └── chat.py            # POST/GET/DELETE /personas/{handle}/chat
│       └── cli.py                 # Typer CLI: `run-server`, `run-consumer-only`
├── tests/
│   ├── conftest.py                # Fixtures: async db, mock firehose, mock claude
│   ├── test_stats_processor.py
│   ├── test_velocity.py
│   ├── test_context_selection.py
│   ├── test_parser.py
│   ├── test_api_stats.py
│   ├── test_api_personas.py
│   └── test_api_chat.py
├── docs/
│   ├── requirements.md
│   ├── engineering-spec.md
│   └── wireframes.html
└── PARKINGLOT.md
```

**Two CLI entrypoints** (via `pyproject.toml [project.scripts]`):
- `bsky-server` — starts FastAPI + firehose consumer (production)
- `bsky-api-only` — starts FastAPI only, no firehose (local dev / testing)

---

## 4. Configuration

```python
# src/bluesky_feed_consumer/config.py
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # Database
    database_url: str  # postgresql+asyncpg://...

    # API
    api_key: str
    host: str = "0.0.0.0"
    port: int = 8000

    # Firehose
    jetstream_url: str = "wss://jetstream2.us-east.bsky.network/subscribe"
    reconnect_base_delay: float = 1.0
    reconnect_max_delay: float = 60.0

    # Stats windows (seconds)
    window_sizes: list[int] = [60, 300, 600]
    top_n_limit: int = 50
    top_languages: int = 20
    velocity_bucket_seconds: int = 2   # unified: same as SSE push interval
    velocity_history_seconds: int = 3600

    # Persona
    max_history_posts: int = 200
    context_posts_count: int = 50
    reply_weight_boost: float = 0.2
    reply_weight_cap: float = 0.7
    recent_ratio: float = 0.6
    persona_poll_interval: float = 30.0

    # Claude
    anthropic_api_key: str
    claude_model: str = "claude-sonnet-4-6-20250514"

    # SSE
    sse_push_interval: float = 2.0

    model_config = SettingsConfigDict(env_file=".env", env_prefix="BSKY_")
```

All config via environment variables (prefixed `BSKY_`) or `.env` file. No YAML/TOML config files.

---

## 5. Database Schema

### 5.1 `stats` schema

```sql
CREATE SCHEMA stats;

-- BIGSERIAL = auto-increment 64-bit integer (Postgres equivalent of MySQL BIGINT AUTO_INCREMENT)
CREATE TABLE stats.snapshots (
    id              BIGSERIAL PRIMARY KEY,
    window_seconds  INT NOT NULL,           -- 60, 300, 600
    window_start    TIMESTAMPTZ NOT NULL,   -- window_end is always window_start + interval(window_seconds)
    post_count      INT NOT NULL DEFAULT 0,
    user_count      INT NOT NULL DEFAULT 0,
    like_count      INT NOT NULL DEFAULT 0,
    repost_count    INT NOT NULL DEFAULT 0,
    reply_count     INT NOT NULL DEFAULT 0,
    prev_post_count     INT,                -- previous window counts (for period-over-period delta)
    prev_user_count     INT,
    prev_like_count     INT,
    prev_repost_count   INT,
    prev_reply_count    INT,
    top_liked       JSONB NOT NULL DEFAULT '[]',
    top_reposted    JSONB NOT NULL DEFAULT '[]',
    language_breakdown  JSONB NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Used by: GET /stats/{window} to fetch most recent snapshot for a given window size
CREATE INDEX idx_snapshots_window ON stats.snapshots(window_seconds, window_start DESC);
```

### 5.2 `chat` schema

```sql
CREATE SCHEMA chat;

CREATE TYPE chat.persona_status AS ENUM ('loading', 'ready', 'error');

-- BIGSERIAL = auto-increment 64-bit integer
CREATE TABLE chat.personas (
    id              BIGSERIAL PRIMARY KEY,
    handle          TEXT NOT NULL UNIQUE,    -- UNIQUE creates an implicit index
    did             TEXT,                   -- Bluesky DID (decentralized identifier)
    display_name    TEXT,
    bio             TEXT,
    avatar_url      TEXT,
    pinned_post_uri TEXT,
    pinned_post_text TEXT,
    status          chat.persona_status NOT NULL DEFAULT 'loading',
    error_message   TEXT,
    total_posts     INT NOT NULL DEFAULT 0,
    total_replies   INT NOT NULL DEFAULT 0,
    last_corpus_update TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TYPE chat.post_type AS ENUM ('post', 'reply', 'quote');

CREATE TABLE chat.persona_posts (
    id              BIGSERIAL PRIMARY KEY,
    persona_id      BIGINT NOT NULL REFERENCES chat.personas(id) ON DELETE CASCADE,
    post_uri        TEXT NOT NULL UNIQUE,
    post_type       chat.post_type NOT NULL,
    text            TEXT NOT NULL,
    parent_text     TEXT,                   -- for replies: parent post text
    quoted_ref      TEXT,                   -- for quotes: brief ref to quoted content
    langs           TEXT[],                 -- language tags from firehose
    posted_at       TIMESTAMPTZ NOT NULL,
    like_count      INT NOT NULL DEFAULT 0,
    repost_count    INT NOT NULL DEFAULT 0,
    reply_count     INT NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Used by: context selection query (fetch posts for a persona, ordered by recency)
-- Also used by: corpus refresh check (most recent post_uri for deduplication)
CREATE INDEX idx_persona_posts_persona ON chat.persona_posts(persona_id, posted_at DESC);

CREATE TYPE chat.message_role AS ENUM ('user', 'assistant');

CREATE TABLE chat.messages (
    id              BIGSERIAL PRIMARY KEY,
    persona_id      BIGINT NOT NULL REFERENCES chat.personas(id) ON DELETE CASCADE,
    role            chat.message_role NOT NULL,
    content         TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Used by: GET /personas/{handle}/chat (load conversation history in chronological order)
-- Also used by: building Claude API messages array for context
CREATE INDEX idx_messages_persona ON chat.messages(persona_id, created_at);
```

### 5.3 Key Queries and Index Usage

| Query | Used By | Index Hit |
|-------|---------|-----------|
| `SELECT * FROM stats.snapshots WHERE window_seconds = $1 ORDER BY window_start DESC LIMIT 1` | GET /stats/{window} | `idx_snapshots_window` |
| `SELECT * FROM chat.personas WHERE handle = $1` | All /personas/{handle}/* endpoints | `personas_handle_key` (UNIQUE) |
| `SELECT * FROM chat.personas WHERE status = 'loading'` | Persona poll loop | Seq scan (few rows, infrequent) |
| `SELECT * FROM chat.persona_posts WHERE persona_id = $1 ORDER BY posted_at DESC` | Context selection | `idx_persona_posts_persona` |
| `SELECT * FROM chat.persona_posts WHERE persona_id = $1 AND post_type = 'reply' ORDER BY posted_at DESC` | Context selection (reply split) | `idx_persona_posts_persona` (partial) |
| `SELECT * FROM chat.messages WHERE persona_id = $1 ORDER BY created_at` | GET /chat, Claude prompt building | `idx_messages_persona` |
| `DELETE FROM chat.messages WHERE persona_id = $1` | DELETE /chat | `idx_messages_persona` |
| `INSERT INTO chat.persona_posts ... ON CONFLICT (post_uri) DO NOTHING` | Corpus refresh (dedup) | `persona_posts_post_uri_key` (UNIQUE) |

---

## 6. API Contracts

### 6.1 Authentication

All endpoints require `X-API-Key` header matching the configured key. Returns `401` if missing/invalid.

### 6.2 Stats Endpoints

**GET /stats/{window}**

Returns the most recent completed window snapshot.

```
Path: window = "1m" | "5m" | "10m"
Query: top_n = int (default 10, max 50)

Response 200:
{
  "window": "1m",
  "window_start": "2026-05-20T15:00:00Z",
  "window_end": "2026-05-20T15:01:00Z",
  "metrics": {
    "post_count": 2400,
    "user_count": 1800,
    "like_count": 5100,
    "repost_count": 892,
    "reply_count": 1200
  },
  "deltas": {
    "post_count": 0.12,
    "user_count": 0.08,
    "like_count": 0.03,
    "repost_count": -0.02,
    "reply_count": 0.05
  },
  "top_liked": [
    {
      "rank": 1,
      "uri": "at://did:plc:.../app.bsky.feed.post/...",
      "author_handle": "elonjet.bsky.social",
      "author_display_name": "Elon Jet",
      "text": "Just landed at SFO...",
      "like_count": 847,
      "posted_at": "2026-05-20T15:00:37Z"
    }
  ],
  "top_reposted": [...],
  "language_breakdown": {
    "en": 0.38,
    "ja": 0.18,
    "pt": 0.12,
    "es": 0.09,
    "de": 0.07,
    "other": 0.16
  }
}
```

**GET /stats/stream**

SSE endpoint. Pushes full current stats every 2 seconds.

```
Event: stats_update
Data: {
  "timestamp": "2026-05-20T15:01:23.456Z",
  "windows": {
    "1m": { ...same shape as GET response... },
    "5m": { ... },
    "10m": { ... }
  },
  "velocity": {
    "current": 43.2,
    "history": [41.0, 42.5, 43.2, ...]  // last hour, 2-sec buckets (1800 points), oldest first
  }
}
```

### 6.3 Persona Endpoints

**POST /personas**

Register a new persona for AI chat.

```
Body: { "handle": "paulg.bsky.social" }

Response 201:
{
  "handle": "paulg.bsky.social",
  "status": "loading",
  "display_name": null,
  "post_count": 0,
  "last_corpus_update": null
}
```

**GET /personas**

List all registered personas.

```
Response 200:
[
  {
    "handle": "paulg.bsky.social",
    "display_name": "Paul Graham",
    "status": "ready",
    "post_count": 200,
    "last_corpus_update": "2026-05-20T15:00:00Z",
    "avatar_url": "https://..."
  }
]
```

**GET /personas/{handle}/status**

```
Response 200:
{
  "handle": "paulg.bsky.social",
  "display_name": "Paul Graham",
  "status": "ready",
  "post_count": 200,
  "reply_count": 87,
  "last_corpus_update": "2026-05-20T15:00:00Z",
  "created_at": "2026-05-20T14:50:00Z"
}
```

### 6.4 Chat Endpoints

**POST /personas/{handle}/chat**

Send a message, receive streaming response via SSE.

```
Body: { "message": "What makes a great startup founder?" }

SSE Events:
  event: token
  data: {"text": "The"}

  event: token
  data: {"text": " best"}

  ...

  event: done
  data: {"full_text": "The best founders...", "context_posts_used": 47}
```

**GET /personas/{handle}/chat**

Retrieve conversation history.

```
Response 200:
{
  "handle": "paulg.bsky.social",
  "messages": [
    {"role": "user", "content": "What makes a great founder?", "created_at": "..."},
    {"role": "assistant", "content": "The best founders...", "created_at": "..."}
  ]
}
```

**DELETE /personas/{handle}/chat**

Clear conversation history. Returns `204 No Content`.

---

## 7. Module Design

### 7.1 Ingestion Module

**consumer.py** — Async WebSocket client for Bluesky Jetstream.

```python
class FirehoseConsumer:
    def __init__(self, settings: Settings, processor: StatsProcessor, db: AsyncSession):
        ...

    async def run(self):
        """Main loop: connect, consume, reconnect on failure."""
        while True:
            try:
                async with websockets.connect(self.settings.jetstream_url) as ws:
                    async for raw_msg in ws:
                        event = parse_event(raw_msg)
                        if event:
                            await self.handle_event(event)
            except (ConnectionClosed, ConnectionError):
                await self._reconnect_with_backoff()

    async def handle_event(self, event: FirehoseEvent):
        self.processor.ingest(event)       # in-memory stats update
        if event.did in self._watched_dids: # persona corpus update
            await self._append_persona_post(event)
```

**parser.py** — Classifies raw Jetstream JSON into typed events.

```python
@dataclass
class FirehoseEvent:
    kind: Literal["post", "reply", "like", "repost", "quote"]
    did: str
    uri: str
    text: str | None
    parent_uri: str | None
    parent_text: str | None
    quoted_ref: str | None
    langs: list[str]
    timestamp: datetime
```

### 7.2 Stats Module

**processor.py** — In-memory rolling window aggregation.

```python
class StatsProcessor:
    def __init__(self, settings: Settings):
        self.windows: dict[int, WindowAccumulator] = {}
        self.velocity = VelocityTracker(settings)

    def ingest(self, event: FirehoseEvent):
        """Called for every firehose event. Updates all active windows."""
        for window in self.windows.values():
            window.record(event)
        self.velocity.record(event)

    def get_current_stats(self) -> dict:
        """Returns current stats for all windows + velocity. Called by SSE endpoint."""
        ...

    def rotate_window(self, window_seconds: int) -> StatSnapshot | None:
        """Called on timer. Closes current window, returns snapshot for DB persistence."""
        ...
```

**WindowAccumulator** tracks per-window counts, top-N heaps, language counters, and unique user sets. On rotation, it produces a `StatSnapshot` and resets.

**velocity.py** — Ring buffer of 2-second buckets (unified with SSE push interval).

```python
class VelocityTracker:
    def __init__(self, settings: Settings):
        # 3600s / 2s = 1800 buckets for 1 hour of history
        self.bucket_count = settings.velocity_history_seconds // settings.velocity_bucket_seconds  # 1800
        self.buckets: deque[int] = deque(maxlen=self.bucket_count)
        self.current_bucket_count: int = 0

    def record(self, event: FirehoseEvent):
        if event.kind in ("post", "reply", "quote"):
            self.current_bucket_count += 1

    def rotate_bucket(self) -> float:
        """Called every 2 seconds by the SSE push loop. Finalizes current bucket,
        returns posts/sec for this bucket, and starts a new one."""
        posts_per_sec = self.current_bucket_count / self.settings.velocity_bucket_seconds
        self.buckets.append(self.current_bucket_count)
        self.current_bucket_count = 0
        return posts_per_sec

    def get_history(self) -> list[float]:
        """Returns posts-per-second for each 2-sec bucket over the last hour."""
        bucket_secs = self.settings.velocity_bucket_seconds
        return [count / bucket_secs for count in self.buckets]
```

### 7.3 Persona Module

**fetcher.py** — Fetches persona profile and post history via AT Protocol.

```python
class PersonaFetcher:
    async def fetch_profile(self, handle: str) -> PersonaProfile:
        """GET /xrpc/app.bsky.actor.getProfile"""
        ...

    async def fetch_posts(self, did: str, limit: int = 200) -> list[PersonaPost]:
        """GET /xrpc/app.bsky.feed.getAuthorFeed with pagination.
        Filters out pure reposts. Classifies as post/reply/quote."""
        ...
```

**context.py** — Selects posts for Claude prompt injection.

```python
class ContextSelector:
    def select(self, posts: list[PersonaPost], count: int = 50) -> list[PersonaPost]:
        """
        Algorithm:
        1. Separate posts into original_posts and replies
        2. Compute reply_weight = min(natural_ratio + 0.2, 0.7)
        3. Allocate: reply_count = round(count * reply_weight), post_count = count - reply_count
        4. For each category:
           - Take 60% most recent
           - Take 40% random sample from remaining
        5. Floor: if replies exist but reply_count == 0, include at least 3
        6. Sort final selection by posted_at descending
        """
        ...

    def format_for_prompt(self, persona: Persona, posts: list[PersonaPost]) -> str:
        """Formats persona profile + selected posts into the system prompt."""
        ...
```

**generator.py** — Claude API integration.

```python
class PersonaGenerator:
    async def stream_response(
        self, persona: Persona, posts: list[PersonaPost],
        conversation_history: list[ChatMessage], user_message: str
    ) -> AsyncIterator[str]:
        """
        Builds system prompt from persona + context posts.
        Sends conversation history + new message to Claude.
        Yields response tokens as they stream.
        """
        system_prompt = self.context_selector.format_for_prompt(persona, posts)
        messages = self._build_messages(conversation_history, user_message)

        async with self.client.messages.stream(
            model=self.settings.claude_model,
            system=system_prompt,
            messages=messages,
            max_tokens=300,
        ) as stream:
            async for text in stream.text_stream:
                yield text
```

### 7.4 Persona System Prompt Template

```
You are roleplaying as {display_name} (@{handle}) on Bluesky.
Respond exactly as this person would — matching their tone, vocabulary, opinions, and personality.

## Profile
{bio}

{if pinned_post:}
## Pinned Post
{pinned_post_text}
{endif}

## Example Posts and Replies
{for post in selected_posts:}
[{post_type} | {posted_at.strftime("%a %I:%M%p")} | {like_count} likes]
{if post_type == "reply":}  Replying to: "{parent_text[:80]}..."
{endif}
{if post_type == "quote":}  Re: "{quoted_ref[:80]}..."
{endif}
{text}
---
{endfor}

## Rules
- Stay in character. Never break character or acknowledge being AI.
- Match their writing style: sentence length, punctuation, emoji usage, capitalization.
- Reference their known interests and opinions when relevant.
- Adapt response length to the conversation — short questions get short answers, thoughtful questions get thoughtful answers.
- If asked about something they haven't posted about, respond consistently with their personality.
```

---

## 8. Application Lifecycle

```python
# src/bluesky_feed_consumer/app.py

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    engine = create_async_engine(settings.database_url)
    processor = StatsProcessor(settings)
    consumer = FirehoseConsumer(settings, processor, engine)
    fetcher = PersonaFetcher(settings)

    # Store in app state for dependency injection
    app.state.processor = processor
    app.state.engine = engine

    # Launch background tasks
    consumer_task = asyncio.create_task(consumer.run())
    snapshot_task = asyncio.create_task(run_snapshot_loop(processor, engine, settings))
    persona_poll_task = asyncio.create_task(run_persona_poll(fetcher, engine, settings))

    yield

    # Shutdown: flush stats, cancel tasks
    await flush_stats_to_db(processor, engine)
    consumer_task.cancel()
    snapshot_task.cancel()
    persona_poll_task.cancel()
    await engine.dispose()
```

**Background loops:**
- `consumer.run()` — persistent WebSocket, reconnects on failure
- `run_snapshot_loop()` — every `min(window_sizes)` seconds, rotates windows and writes snapshots to Postgres
- `run_persona_poll()` — every 30 seconds, checks for personas in `loading` state and fetches their history

---

## 9. SSE Implementation

Using `sse-starlette` for both live stats and chat streaming.

### Live Stats (GET /stats/stream)

Uses a snapshot + delta pattern to minimize bandwidth:

1. **On initial connection**: send a `snapshot` event with full velocity history (1800 data points) and current window stats
2. **Every 2 seconds**: send an `update` event with only the new velocity data point and current stats. Client appends to its local history array and trims to 1800 entries.

```
Initial event:
  event: snapshot
  data: { "timestamp": "...", "windows": {...}, "velocity_history": [41.0, 42.5, ...] }

Subsequent events (every 2s):
  event: update
  data: { "timestamp": "...", "windows": {...}, "velocity_current": 43.2 }
```

- Reads directly from in-memory `StatsProcessor` — no DB query
- Velocity bucket rotation happens in the SSE push loop (every 2 seconds)
- Client discards events with timestamps older than currently displayed (handles slow-network buffering)
- All time resolutions are unified at 2 seconds — no separate 5-second bucket granularity

### Chat Streaming (POST /personas/{handle}/chat)

- Streams Claude API tokens as SSE events
- `token` events contain incremental text
- `done` event signals completion with full response text and metadata
- Response is persisted to `chat.messages` after streaming completes

---

## 10. Firehose Event Processing

### Jetstream Message Format

Bluesky Jetstream sends JSON over WebSocket:
```json
{
  "did": "did:plc:abc123",
  "time_us": 1716220800000000,
  "kind": "commit",
  "commit": {
    "rev": "...",
    "operation": "create",
    "collection": "app.bsky.feed.post",
    "rkey": "...",
    "record": {
      "$type": "app.bsky.feed.post",
      "text": "Hello world",
      "langs": ["en"],
      "createdAt": "2026-05-20T15:00:00Z",
      "reply": { "parent": {...}, "root": {...} },
      "embed": { "$type": "app.bsky.embed.record", ... }
    }
  }
}
```

### Event Classification

| Collection | Operation | Has `reply`? | Has `embed.record`? | Classification |
|-----------|-----------|-------------|---------------------|---------------|
| `app.bsky.feed.post` | create | No | No | **post** |
| `app.bsky.feed.post` | create | Yes | No | **reply** |
| `app.bsky.feed.post` | create | No | Yes (with text) | **quote** |
| `app.bsky.feed.like` | create | — | — | **like** |
| `app.bsky.feed.repost` | create | — | — | **repost** (stats only, excluded from persona context) |

---

## 11. Deployment

### Deployable Artifact

No containers for initial development. The deployable artifact is the Python package itself, installed from the git repo via `uv`.

```
# On the VM:
git clone <repo-url> /opt/bluesky-feed-consumer
cd /opt/bluesky-feed-consumer
uv sync --frozen
```

The `bsky-server` CLI entrypoint is installed by `uv sync` (defined in `pyproject.toml [project.scripts]`).

### Compute Engine Setup

- **Instance**: e2-small (0.5 vCPU, 2GB RAM), Debian 12
- **Region**: us-central1 (close to Jetstream servers on US East, close to Cloud SQL)
- **Software**: Python 3.12+ and `uv` installed via startup script
- **Secrets**: `BSKY_ANTHROPIC_API_KEY` and `BSKY_API_KEY` stored in GCP Secret Manager, loaded into `/opt/bluesky-feed-consumer/.env` by a startup script
- **Firewall**: Allow inbound TCP 8000 from app client IPs (or 0.0.0.0/0 for demo)

### systemd Service

```ini
# /etc/systemd/system/bsky-server.service
[Unit]
Description=Bluesky Feed Consumer
After=network.target

[Service]
Type=simple
User=bsky
WorkingDirectory=/opt/bluesky-feed-consumer
ExecStart=/opt/bluesky-feed-consumer/.venv/bin/bsky-server
EnvironmentFile=/opt/bluesky-feed-consumer/.env
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### Deploy to Production

```bash
#!/bin/bash
# deploy.sh — run from local machine
set -e

VM_NAME="bsky-consumer"
ZONE="us-central1-a"

gcloud compute ssh $VM_NAME --zone=$ZONE --command="
  cd /opt/bluesky-feed-consumer &&
  git pull origin main &&
  uv sync --frozen &&
  sudo systemctl restart bsky-server
"

echo "Deployed. Checking health..."
sleep 3
gcloud compute ssh $VM_NAME --zone=$ZONE --command="systemctl status bsky-server --no-pager"
```

Containerization (Dockerfile) will be added later when needed.

### Cloud SQL Setup

- **Instance**: db-f1-micro (shared vCPU, 0.6GB RAM), PostgreSQL 16
- **Region**: us-central1 (same as CE)
- **Connection**: Private IP within VPC (no public IP needed)
- **Storage**: 10GB SSD (auto-resize)
- **Backups**: Automated daily (default)

### Estimated Monthly Cost

| Resource | Cost |
|----------|------|
| Compute Engine e2-small | ~$12/month |
| Cloud SQL db-f1-micro | ~$7/month |
| Network egress | ~$1/month |
| Claude API (Sonnet, demo usage) | ~$5-10/month |
| **Total** | **~$25-30/month** |

Well within the $100/month budget.

---

## 12. Testing Strategy

### Coverage Tooling

Tests run with **pytest-cov** (wraps [coverage.py](https://coverage.readthedocs.io/)) providing both **line** and **branch** coverage on every `make test` invocation. The report shows missing lines and partially-covered branches directly in the terminal.

```bash
make test   # runs: pytest --cov=bluesky_feed_consumer --cov-branch --cov-report=term-missing
```

Coverage targets (per module type):
- **Pure logic modules** (parser, processor, context, velocity): 95–100%
- **API route handlers**: 70–85% (uncovered lines are SSE streaming paths requiring mock Claude)
- **I/O modules** (consumer, fetcher, poll, snapshot): deferred to Phase 4 integration tests with mocked externals

### Unit Tests

| Module | Test Focus | Tests |
|--------|-----------|-------|
| `test_parser.py` | Event classification from raw JSON: post, reply, quote, like, repost, edge cases (missing timestamp, non-string text, non-record embed types) | 16 |
| `test_stats_processor.py` | Window accumulation, rotation, period-over-period deltas (including zero-division), top-N heap (cap, replacement, ordering) | 18 |
| `test_velocity.py` | Ring buffer behavior, bucket rotation, posts/sec calculation, cap at maxlen | 7 |
| `test_context_selection.py` | Reply ratio calculation, recent/sampled split, floor enforcement, slot redistribution, format_for_prompt (bio fallback, pinned post, reply/quote context, display name fallback) | 16 |
| `test_generator.py` | `_build_messages()` helper: empty history, role mapping, ordering | 3 |
| `test_config.py` | Default values, environment variable override | 2 |

### API Integration Tests (FastAPI TestClient + SQLite)

| Test File | Coverage | Tests |
|-----------|---------|-------|
| `test_auth.py` | Health endpoint (no auth), reject missing key, reject bad key, accept valid key | 6 |
| `test_api_personas.py` | POST/GET personas, duplicate (409), status, 404, chat with non-ready persona (409), empty history, delete history | 8 |

Test database uses SQLite (async via aiosqlite) with `schema_translate_map` to strip Postgres schema prefixes. Tables are created fresh per test via `Base.metadata.create_all`.

### Mocks (Phase 4)

- **Firehose**: Mock WebSocket server feeding synthetic events for consumer tests
- **Claude API**: Mock Anthropic client returning canned streaming responses
- **AT Protocol**: Mock HTTP responses for profile and feed fetching

These will close the remaining coverage gap in I/O-heavy modules (`consumer.py`, `fetcher.py`, `poll.py`, `snapshot.py`).

---

## 13. Error Handling

| Scenario | Behavior |
|----------|----------|
| Firehose disconnect | Exponential backoff reconnect (1s, 2s, 4s, ..., 60s max). Log warning. Stats windows may have gaps. |
| Claude API timeout | Return SSE error event `{"error": "Response timeout"}`. Client shows retry option. |
| Claude API rate limit | Return SSE error event with retry-after hint. |
| Persona fetch failure | Set persona status to `error` with message. Client shows error state. Allow re-registration. |
| Invalid window param | Return 400 with valid options. |
| Persona not ready | POST /chat returns 409 Conflict with status info. |
| DB connection failure | Log error. Stats continue in-memory. API returns 503. Retry DB connection on background loop. |

---

## 14. Development Environment

### 14.1 Source Control

- **Repository**: GitHub (private), `tedd4u/bluesky-feed-consumer`
- **Branch strategy**: feature branches off `master`, merged via pull request
- **Commit convention**: imperative subject line, body explains "why"

### 14.2 CI (GitHub Actions)

Runs on every push to `master` and on all pull requests:

1. **Lint** — `ruff check` (import ordering, unused variables, style)
2. **Type check** — `mypy --strict` (full type coverage)
3. **Test** — `pytest` (unit + integration tests)

Workflow: `.github/workflows/ci.yml`

All three must pass before a PR can merge. Locally, developers run the same checks via `make check`.

### 14.3 Local Dev Workflow

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"      # or: uv sync --all-extras
cp .env.example .env         # fill in DB URL + API keys
createdb bsky && alembic upgrade head
make check                   # lint + typecheck + test
bsky-api-only                # run API without firehose
```

### 14.4 Tooling

| Tool | Purpose | Config Location |
|------|---------|-----------------|
| ruff | Lint + format | `pyproject.toml [tool.ruff]` |
| mypy | Type checking (strict) | `pyproject.toml [tool.mypy]` |
| pytest | Tests | `pyproject.toml [tool.pytest]` |
| pytest-cov | Line + branch coverage | Flags in `Makefile` test target |
| alembic | DB migrations | `alembic.ini` + `alembic/` |
| make | Task runner | `Makefile` |

---

## 15. Implementation Order

Each phase includes its own tests. A phase is not complete until `make check` passes (lint + typecheck + tests). All work is done on feature branches and merged via PR with CI green.

### Phase 1: Foundation ✅
1. Project scaffolding (pyproject.toml, directory structure, config)
2. Database models + Alembic migrations
3. FastAPI app skeleton with auth middleware
4. CI: GitHub Actions running `make check` on PRs
5. **Tests**: config validation, auth middleware (reject missing/bad key, accept good key)

### Phase 2: Firehose + Stats ✅
6. Jetstream WebSocket consumer + event parser
7. Stats processor (in-memory windowed aggregation)
8. Velocity tracker (ring buffer)
9. Snapshot writer (Postgres persistence)
10. Wire into app lifespan with background tasks
11. **Tests**: event classification, window accumulation, rotation, deltas, velocity ring buffer

*Stats API endpoints (GET /stats/{window}, GET /stats/stream) are stubbed; full implementation in Phase 4.*

### Phase 3: Persona Chat ✅
12. AT Protocol fetcher (profile + post history)
13. Persona CRUD endpoints
14. Persona background poll loop
15. Context selection algorithm
16. Claude API integration (streaming)
17. Chat endpoints (POST/GET/DELETE)
18. **Tests**: context selection (ratio calc, recent/sampled split, repost exclusion, floor), persona CRUD, chat streaming, mock AT Protocol + mock Claude

### Phase 4: API + Polish ✅
19. Stats API endpoints (GET /stats/{window} with real data, SSE stream)
20. Error handling for all edge cases
21. Integration tests with mocked externals (consumer, fetcher, poll loop)
22. **Tests**: 107 passing, 85% overall coverage

### Phase 5: Deploy
23. GCP infrastructure setup (CE, Cloud SQL, Secret Manager, firewall)
24. systemd service + deploy.sh script
25. **CD: extend GitHub Actions to deploy on merge to `master`** — if CI passes, SSH to CE and run `git pull && uv sync --frozen && sudo systemctl restart bsky-server` (continuous deployment, no manual deploy step)
26. GCP Cloud Monitoring integration (structured logging, custom metrics, dashboard JSON via `gcloud`)
27. **Tests**: end-to-end smoke test on GCP (connect firehose, verify stats flow, register persona, send chat message)

*Infrastructure is managed via shell scripts + `gcloud` CLI, not Terraform. The infra footprint (one CE instance, one Cloud SQL, one Secret Manager secret) doesn't justify a separate IaC tool.*
