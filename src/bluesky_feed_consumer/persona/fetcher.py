"""Fetch Bluesky profile and post history via the AT Protocol public API."""

from __future__ import annotations

import datetime
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx

from bluesky_feed_consumer.config import Settings

logger = logging.getLogger(__name__)

_BASE_URL = "https://public.api.bsky.app/xrpc"


@dataclass(frozen=True, slots=True)
class FetchedProfile:
    did: str
    handle: str
    display_name: str
    bio: str
    avatar_url: str


@dataclass(frozen=True, slots=True)
class FetchedPost:
    uri: str
    post_type: str  # "post", "reply", "quote"
    text: str
    parent_text: str | None
    quoted_ref: str | None
    langs: list[str]
    posted_at: datetime.datetime
    like_count: int
    repost_count: int
    reply_count: int


class PersonaFetcher:
    """Fetches persona profile and post history from the Bluesky public API."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client = httpx.AsyncClient(timeout=30.0)

    async def close(self) -> None:
        await self._client.aclose()

    async def fetch_profile(self, handle: str) -> FetchedProfile:
        """GET /xrpc/app.bsky.actor.getProfile"""
        resp = await self._client.get(
            f"{_BASE_URL}/app.bsky.actor.getProfile",
            params={"actor": handle},
        )
        resp.raise_for_status()
        data = resp.json()

        return FetchedProfile(
            did=data.get("did", ""),
            handle=data.get("handle", handle),
            display_name=data.get("displayName", ""),
            bio=data.get("description", ""),
            avatar_url=data.get("avatar", ""),
        )

    async def fetch_posts_paginated(
        self,
        did: str,
        limit: int | None = None,
        page_size: int = 20,
    ) -> AsyncIterator[list[FetchedPost]]:
        """Yield pages of posts so callers can persist incrementally.

        Each yielded list contains up to *page_size* parsed posts (after
        filtering out pure reposts).  The Bluesky API caps a single request
        at 100, so *page_size* is clamped to that.
        """
        if limit is None:
            limit = self.settings.max_history_posts
        page_size = min(page_size, 100)  # API hard max

        total = 0
        cursor: str | None = None

        while total < limit:
            params: dict[str, str | int] = {
                "actor": did,
                "limit": page_size,
                "filter": "posts_and_author_threads",
            }
            if cursor:
                params["cursor"] = cursor

            resp = await self._client.get(
                f"{_BASE_URL}/app.bsky.feed.getAuthorFeed",
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()

            feed = data.get("feed", [])
            if not feed:
                break

            page: list[FetchedPost] = []
            for item in feed:
                parsed = _parse_feed_item(item)
                if parsed is not None:
                    page.append(parsed)
                    total += 1
                    if total >= limit:
                        break

            if page:
                yield page

            cursor = data.get("cursor")
            if not cursor:
                break

        logger.info("Fetched %d posts for %s", total, did)

    async def fetch_posts(self, did: str, limit: int | None = None) -> list[FetchedPost]:
        """Fetch post history via getAuthorFeed with pagination.

        Convenience wrapper around :meth:`fetch_posts_paginated` that
        collects all pages into a single list.
        """
        posts: list[FetchedPost] = []
        async for page in self.fetch_posts_paginated(did, limit):
            posts.extend(page)
        return posts


def _parse_feed_item(item: dict[str, object]) -> FetchedPost | None:
    """Parse a single feed item into a FetchedPost, or None if it's a pure repost."""
    # Skip pure reposts (reason == "app.bsky.feed.defs#reasonRepost")
    reason = item.get("reason")
    if isinstance(reason, dict) and reason.get("$type") == "app.bsky.feed.defs#reasonRepost":
        return None

    post_data = item.get("post")
    if not isinstance(post_data, dict):
        return None

    record = post_data.get("record")
    if not isinstance(record, dict):
        return None

    uri = post_data.get("uri", "")
    if not isinstance(uri, str):
        return None

    text = record.get("text", "")
    if not isinstance(text, str):
        text = ""

    raw_langs = record.get("langs")
    langs = list(raw_langs) if isinstance(raw_langs, list) else []

    # Engagement counts
    like_count = _get_int(post_data, "likeCount")
    repost_count = _get_int(post_data, "repostCount")
    reply_count_val = _get_int(post_data, "replyCount")

    # Timestamp
    created_at = record.get("createdAt", "")
    if isinstance(created_at, str) and created_at:
        try:
            posted_at = datetime.datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except ValueError:
            posted_at = datetime.datetime.now(datetime.UTC)
    else:
        posted_at = datetime.datetime.now(datetime.UTC)

    # Classify: reply, quote, or post
    reply_ref = record.get("reply")
    if isinstance(reply_ref, dict):
        # Try to get parent post text from the reply structure
        reply_data = item.get("reply")
        parent_text = _extract_parent_text(reply_data)
        return FetchedPost(
            uri=uri,
            post_type="reply",
            text=text,
            parent_text=parent_text,
            quoted_ref=None,
            langs=langs,
            posted_at=posted_at,
            like_count=like_count,
            repost_count=repost_count,
            reply_count=reply_count_val,
        )

    embed = record.get("embed")
    if isinstance(embed, dict):
        embed_type = embed.get("$type", "")
        if embed_type in ("app.bsky.embed.record", "app.bsky.embed.recordWithMedia"):
            embed_record = embed.get("record", {})
            quoted_uri = embed_record.get("uri", "") if isinstance(embed_record, dict) else ""
            if text:
                return FetchedPost(
                    uri=uri,
                    post_type="quote",
                    text=text,
                    parent_text=None,
                    quoted_ref=quoted_uri,
                    langs=langs,
                    posted_at=posted_at,
                    like_count=like_count,
                    repost_count=repost_count,
                    reply_count=reply_count_val,
                )

    return FetchedPost(
        uri=uri,
        post_type="post",
        text=text,
        parent_text=None,
        quoted_ref=None,
        langs=langs,
        posted_at=posted_at,
        like_count=like_count,
        repost_count=repost_count,
        reply_count=reply_count_val,
    )


def _extract_parent_text(reply_data: object) -> str | None:
    """Extract the parent post's text from the feed item's reply structure."""
    if not isinstance(reply_data, dict):
        return None
    parent = reply_data.get("parent")
    if not isinstance(parent, dict):
        return None
    record = parent.get("record")
    if not isinstance(record, dict):
        return None
    text = record.get("text")
    return text if isinstance(text, str) else None


def _get_int(data: dict[str, object], key: str) -> int:
    val = data.get(key, 0)
    return val if isinstance(val, int) else 0
