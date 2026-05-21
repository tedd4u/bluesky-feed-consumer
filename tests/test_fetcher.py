"""Tests for the AT Protocol persona fetcher with mocked HTTP responses."""

import datetime
from unittest.mock import AsyncMock, Mock, patch

import pytest

from bluesky_feed_consumer.config import Settings
from bluesky_feed_consumer.persona.fetcher import (
    PersonaFetcher,
    _extract_parent_text,
    _parse_feed_item,
)


def _settings() -> Settings:
    return Settings(max_history_posts=10)


class TestParseFeedItem:
    """Unit tests for feed item parsing (no HTTP needed)."""

    def test_simple_post(self) -> None:
        item = {
            "post": {
                "uri": "at://did:plc:x/app.bsky.feed.post/abc",
                "record": {
                    "text": "Hello world",
                    "langs": ["en"],
                    "createdAt": "2024-06-01T12:00:00Z",
                },
                "likeCount": 5,
                "repostCount": 2,
                "replyCount": 1,
            }
        }
        result = _parse_feed_item(item)
        assert result is not None
        assert result.post_type == "post"
        assert result.text == "Hello world"
        assert result.langs == ["en"]
        assert result.like_count == 5
        assert result.posted_at.year == 2024

    def test_reply_post(self) -> None:
        item = {
            "post": {
                "uri": "at://did:plc:x/app.bsky.feed.post/def",
                "record": {
                    "text": "I agree!",
                    "reply": {"parent": {"uri": "at://parent"}, "root": {"uri": "at://root"}},
                    "createdAt": "2024-06-01T12:00:00Z",
                },
                "likeCount": 0,
                "repostCount": 0,
                "replyCount": 0,
            },
            "reply": {
                "parent": {
                    "record": {"text": "Original post text here"},
                },
            },
        }
        result = _parse_feed_item(item)
        assert result is not None
        assert result.post_type == "reply"
        assert result.parent_text == "Original post text here"

    def test_quote_post(self) -> None:
        item = {
            "post": {
                "uri": "at://did:plc:x/app.bsky.feed.post/ghi",
                "record": {
                    "text": "This is so true",
                    "embed": {
                        "$type": "app.bsky.embed.record",
                        "record": {"uri": "at://quoted-post"},
                    },
                    "createdAt": "2024-06-01T12:00:00Z",
                },
                "likeCount": 0,
                "repostCount": 0,
                "replyCount": 0,
            }
        }
        result = _parse_feed_item(item)
        assert result is not None
        assert result.post_type == "quote"
        assert result.quoted_ref == "at://quoted-post"

    def test_pure_repost_filtered(self) -> None:
        item = {
            "reason": {"$type": "app.bsky.feed.defs#reasonRepost"},
            "post": {
                "uri": "at://did:plc:x/app.bsky.feed.post/jkl",
                "record": {"text": "Reposted content", "createdAt": "2024-06-01T12:00:00Z"},
                "likeCount": 0,
                "repostCount": 0,
                "replyCount": 0,
            },
        }
        assert _parse_feed_item(item) is None

    def test_missing_post_data(self) -> None:
        assert _parse_feed_item({}) is None
        assert _parse_feed_item({"post": "not a dict"}) is None

    def test_missing_record(self) -> None:
        item = {"post": {"uri": "at://x", "likeCount": 0}}
        assert _parse_feed_item(item) is None

    def test_invalid_timestamp_fallback(self) -> None:
        item = {
            "post": {
                "uri": "at://did:plc:x/app.bsky.feed.post/ts",
                "record": {"text": "Bad ts", "createdAt": "not-a-date"},
                "likeCount": 0,
                "repostCount": 0,
                "replyCount": 0,
            }
        }
        result = _parse_feed_item(item)
        assert result is not None
        # Should fall back to now()
        assert (datetime.datetime.now(datetime.UTC) - result.posted_at).total_seconds() < 2

    def test_non_string_text(self) -> None:
        item = {
            "post": {
                "uri": "at://did:plc:x/app.bsky.feed.post/nt",
                "record": {"text": 123, "createdAt": "2024-06-01T12:00:00Z"},
                "likeCount": 0,
                "repostCount": 0,
                "replyCount": 0,
            }
        }
        result = _parse_feed_item(item)
        assert result is not None
        assert result.text == ""


class TestExtractParentText:
    def test_valid_reply_data(self) -> None:
        reply_data = {"parent": {"record": {"text": "Parent says hi"}}}
        assert _extract_parent_text(reply_data) == "Parent says hi"

    def test_missing_parent(self) -> None:
        assert _extract_parent_text({"parent": None}) is None
        assert _extract_parent_text({}) is None

    def test_non_dict_input(self) -> None:
        assert _extract_parent_text(None) is None
        assert _extract_parent_text("string") is None


class TestPersonaFetcherHTTP:
    @pytest.mark.asyncio
    async def test_fetch_profile(self) -> None:
        settings = _settings()
        fetcher = PersonaFetcher(settings)

        mock_response = Mock()
        mock_response.json.return_value = {
            "did": "did:plc:abc123",
            "handle": "alice.bsky.social",
            "displayName": "Alice",
            "description": "I like cats",
            "avatar": "https://cdn.bsky.app/avatar.jpg",
        }
        mock_response.raise_for_status = Mock()

        with patch.object(
            fetcher._client, "get", new_callable=AsyncMock, return_value=mock_response
        ) as mock_get:
            profile = await fetcher.fetch_profile("alice.bsky.social")
            mock_get.assert_called_once()

        assert profile.did == "did:plc:abc123"
        assert profile.display_name == "Alice"
        assert profile.bio == "I like cats"
        await fetcher.close()

    @pytest.mark.asyncio
    async def test_fetch_posts_paginates(self) -> None:
        settings = Settings(max_history_posts=5)
        fetcher = PersonaFetcher(settings)

        page1 = {
            "feed": [
                {
                    "post": {
                        "uri": f"at://x/app.bsky.feed.post/{i}",
                        "record": {"text": f"Post {i}", "createdAt": "2024-06-01T12:00:00Z"},
                        "likeCount": 0,
                        "repostCount": 0,
                        "replyCount": 0,
                    }
                }
                for i in range(3)
            ],
            "cursor": "page2",
        }
        page2 = {
            "feed": [
                {
                    "post": {
                        "uri": f"at://x/app.bsky.feed.post/{i + 3}",
                        "record": {"text": f"Post {i + 3}", "createdAt": "2024-06-01T12:00:00Z"},
                        "likeCount": 0,
                        "repostCount": 0,
                        "replyCount": 0,
                    }
                }
                for i in range(3)
            ],
            "cursor": None,
        }

        call_count = 0

        async def mock_get(*args: object, **kwargs: object) -> Mock:
            nonlocal call_count
            call_count += 1
            resp = Mock()
            resp.json.return_value = page1 if call_count == 1 else page2
            resp.raise_for_status = Mock()
            return resp

        with patch.object(fetcher._client, "get", side_effect=mock_get):
            posts = await fetcher.fetch_posts("did:plc:abc", limit=5)

        assert len(posts) == 5
        assert call_count == 2  # paginated
        await fetcher.close()

    @pytest.mark.asyncio
    async def test_fetch_posts_stops_on_empty_page(self) -> None:
        settings = _settings()
        fetcher = PersonaFetcher(settings)

        async def mock_get(*args: object, **kwargs: object) -> Mock:
            resp = Mock()
            resp.json.return_value = {"feed": []}
            resp.raise_for_status = Mock()
            return resp

        with patch.object(fetcher._client, "get", side_effect=mock_get):
            posts = await fetcher.fetch_posts("did:plc:empty")

        assert posts == []
        await fetcher.close()
