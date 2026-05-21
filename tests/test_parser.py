"""Tests for Jetstream event parsing and classification."""

import json

from bluesky_feed_consumer.ingestion.parser import parse_event


def _make_msg(
    collection: str,
    record: dict | None = None,
    did: str = "did:plc:test123",
    rkey: str = "abc",
    time_us: int = 1716220800_000_000,
) -> str:
    msg = {
        "did": did,
        "time_us": time_us,
        "kind": "commit",
        "commit": {
            "rev": "rev1",
            "operation": "create",
            "collection": collection,
            "rkey": rkey,
            "record": record or {},
        },
    }
    return json.dumps(msg)


class TestParsePost:
    def test_simple_post(self) -> None:
        raw = _make_msg(
            "app.bsky.feed.post",
            {"text": "Hello world", "langs": ["en"]},
        )
        event = parse_event(raw)
        assert event is not None
        assert event.kind == "post"
        assert event.text == "Hello world"
        assert event.langs == ["en"]
        assert event.did == "did:plc:test123"
        assert "app.bsky.feed.post" in event.uri

    def test_post_no_langs(self) -> None:
        raw = _make_msg("app.bsky.feed.post", {"text": "No lang"})
        event = parse_event(raw)
        assert event is not None
        assert event.kind == "post"
        assert event.langs == []

    def test_reply(self) -> None:
        raw = _make_msg(
            "app.bsky.feed.post",
            {
                "text": "Great point!",
                "reply": {
                    "parent": {"uri": "at://did:plc:parent/app.bsky.feed.post/xyz"},
                    "root": {"uri": "at://did:plc:root/app.bsky.feed.post/xyz"},
                },
            },
        )
        event = parse_event(raw)
        assert event is not None
        assert event.kind == "reply"
        assert event.text == "Great point!"
        assert event.parent_uri == "at://did:plc:parent/app.bsky.feed.post/xyz"

    def test_quote_with_text(self) -> None:
        raw = _make_msg(
            "app.bsky.feed.post",
            {
                "text": "This is so true!",
                "embed": {
                    "$type": "app.bsky.embed.record",
                    "record": {"uri": "at://did:plc:quoted/app.bsky.feed.post/q1"},
                },
            },
        )
        event = parse_event(raw)
        assert event is not None
        assert event.kind == "quote"
        assert event.text == "This is so true!"
        assert event.quoted_ref == "at://did:plc:quoted/app.bsky.feed.post/q1"

    def test_quote_without_text_is_post(self) -> None:
        """An embed without text in the post is classified as a regular post."""
        raw = _make_msg(
            "app.bsky.feed.post",
            {
                "text": "",
                "embed": {
                    "$type": "app.bsky.embed.record",
                    "record": {"uri": "at://did:plc:quoted/app.bsky.feed.post/q1"},
                },
            },
        )
        event = parse_event(raw)
        assert event is not None
        assert event.kind == "post"

    def test_record_with_media_embed_quote(self) -> None:
        raw = _make_msg(
            "app.bsky.feed.post",
            {
                "text": "Look at this with media",
                "embed": {
                    "$type": "app.bsky.embed.recordWithMedia",
                    "record": {"uri": "at://did:plc:quoted/app.bsky.feed.post/m1"},
                },
            },
        )
        event = parse_event(raw)
        assert event is not None
        assert event.kind == "quote"
        assert event.quoted_ref == "at://did:plc:quoted/app.bsky.feed.post/m1"


class TestParseLikeRepost:
    def test_like(self) -> None:
        raw = _make_msg(
            "app.bsky.feed.like",
            {"subject": {"uri": "at://did:plc:target/app.bsky.feed.post/x"}},
        )
        event = parse_event(raw)
        assert event is not None
        assert event.kind == "like"
        assert event.did == "did:plc:test123"
        assert "app.bsky.feed.like" in event.uri

    def test_repost(self) -> None:
        raw = _make_msg(
            "app.bsky.feed.repost",
            {"subject": {"uri": "at://did:plc:target/app.bsky.feed.post/x"}},
        )
        event = parse_event(raw)
        assert event is not None
        assert event.kind == "repost"
        assert event.did == "did:plc:test123"
        assert "app.bsky.feed.repost" in event.uri


class TestParseEdgeCases:
    def test_invalid_json(self) -> None:
        assert parse_event("not json") is None

    def test_non_commit_kind(self) -> None:
        msg = json.dumps({"kind": "identity", "did": "did:plc:test"})
        assert parse_event(msg) is None

    def test_non_create_operation(self) -> None:
        msg = json.dumps(
            {
                "did": "did:plc:test",
                "kind": "commit",
                "commit": {"operation": "delete", "collection": "app.bsky.feed.post"},
            }
        )
        assert parse_event(msg) is None

    def test_unknown_collection(self) -> None:
        raw = _make_msg("app.bsky.graph.follow", {})
        assert parse_event(raw) is None

    def test_timestamp_parsing(self) -> None:
        raw = _make_msg(
            "app.bsky.feed.post",
            {"text": "ts test"},
            time_us=1716220800_000_000,
        )
        event = parse_event(raw)
        assert event is not None
        assert event.timestamp.year == 2024

    def test_missing_timestamp_uses_now(self) -> None:
        """When time_us is missing, falls back to current time."""
        msg = {
            "did": "did:plc:test",
            "kind": "commit",
            "commit": {
                "rev": "rev1",
                "operation": "create",
                "collection": "app.bsky.feed.post",
                "rkey": "abc",
                "record": {"text": "no ts"},
            },
        }
        event = parse_event(json.dumps(msg))
        assert event is not None
        # Should be recent (within last second)
        import datetime

        assert (datetime.datetime.now(datetime.UTC) - event.timestamp).total_seconds() < 2

    def test_non_string_text_treated_as_empty(self) -> None:
        """If record text is not a string (e.g. int), it becomes empty string."""
        raw = _make_msg("app.bsky.feed.post", {"text": 12345})
        event = parse_event(raw)
        assert event is not None
        assert event.text == ""

    def test_embed_with_non_record_type(self) -> None:
        """Embed of type images (not record/recordWithMedia) is just a post."""
        raw = _make_msg(
            "app.bsky.feed.post",
            {
                "text": "Check this photo",
                "embed": {
                    "$type": "app.bsky.embed.images",
                    "images": [],
                },
            },
        )
        event = parse_event(raw)
        assert event is not None
        assert event.kind == "post"
        assert event.quoted_ref is None
