"""Parse raw Jetstream WebSocket JSON into typed FirehoseEvent."""

from __future__ import annotations

import datetime
import json
import logging
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger(__name__)

EventKind = Literal["post", "reply", "like", "repost", "quote"]


@dataclass(frozen=True, slots=True)
class FirehoseEvent:
    kind: EventKind
    did: str
    uri: str
    text: str | None = None
    parent_uri: str | None = None
    quoted_ref: str | None = None
    langs: list[str] = field(default_factory=list)
    timestamp: datetime.datetime = field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC)
    )


# Collection constants
_COLLECTION_POST = "app.bsky.feed.post"
_COLLECTION_LIKE = "app.bsky.feed.like"
_COLLECTION_REPOST = "app.bsky.feed.repost"


def parse_event(raw: str | bytes) -> FirehoseEvent | None:
    """Parse a raw Jetstream message into a FirehoseEvent, or None if irrelevant."""
    try:
        msg = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.debug("Failed to parse JSON from firehose message")
        return None

    if msg.get("kind") != "commit":
        return None

    commit = msg.get("commit")
    if not commit or commit.get("operation") != "create":
        return None

    did: str = msg.get("did", "")
    collection: str = commit.get("collection", "")
    rkey: str = commit.get("rkey", "")
    record: dict[str, object] = commit.get("record", {})

    timestamp = _parse_timestamp(msg)

    if collection == _COLLECTION_LIKE:
        return FirehoseEvent(
            kind="like",
            did=did,
            uri=f"at://{did}/{collection}/{rkey}",
            timestamp=timestamp,
        )

    if collection == _COLLECTION_REPOST:
        return FirehoseEvent(
            kind="repost",
            did=did,
            uri=f"at://{did}/{collection}/{rkey}",
            timestamp=timestamp,
        )

    if collection == _COLLECTION_POST:
        return _parse_post(did, rkey, record, timestamp)

    return None


def _parse_post(
    did: str,
    rkey: str,
    record: dict[str, object],
    timestamp: datetime.datetime,
) -> FirehoseEvent:
    """Classify a post record as post, reply, or quote."""
    uri = f"at://{did}/{_COLLECTION_POST}/{rkey}"
    text = record.get("text", "")
    if not isinstance(text, str):
        text = ""

    raw_langs = record.get("langs")
    langs = list(raw_langs) if isinstance(raw_langs, list) else []

    reply = record.get("reply")
    if isinstance(reply, dict):
        parent = reply.get("parent", {})
        parent_uri = parent.get("uri", "") if isinstance(parent, dict) else ""
        return FirehoseEvent(
            kind="reply",
            did=did,
            uri=uri,
            text=text,
            parent_uri=parent_uri,
            langs=langs,
            timestamp=timestamp,
        )

    embed = record.get("embed")
    if isinstance(embed, dict):
        embed_type = embed.get("$type", "")
        if embed_type in (
            "app.bsky.embed.record",
            "app.bsky.embed.recordWithMedia",
        ):
            embed_record = embed.get("record", {})
            quoted_uri = (
                embed_record.get("uri", "")
                if isinstance(embed_record, dict)
                else ""
            )
            if text:
                return FirehoseEvent(
                    kind="quote",
                    did=did,
                    uri=uri,
                    text=text,
                    quoted_ref=quoted_uri,
                    langs=langs,
                    timestamp=timestamp,
                )

    return FirehoseEvent(
        kind="post",
        did=did,
        uri=uri,
        text=text,
        langs=langs,
        timestamp=timestamp,
    )


def _parse_timestamp(msg: dict[str, object]) -> datetime.datetime:
    """Extract timestamp from Jetstream message (time_us field, microseconds)."""
    time_us = msg.get("time_us")
    if isinstance(time_us, int):
        return datetime.datetime.fromtimestamp(
            time_us / 1_000_000, tz=datetime.UTC
        )
    return datetime.datetime.now(datetime.UTC)
