"""Tests for the context selection algorithm."""

import datetime

from bluesky_feed_consumer.config import Settings
from bluesky_feed_consumer.models.chat import Persona, PersonaPost, PersonaStatus, PostType
from bluesky_feed_consumer.persona.context import ContextSelector


def _settings(**kwargs) -> Settings:
    defaults = {
        "context_posts_count": 10,
        "reply_weight_boost": 0.2,
        "reply_weight_cap": 0.7,
        "recent_ratio": 0.6,
    }
    defaults.update(kwargs)
    return Settings(**defaults)


def _post(
    post_type: str = "post",
    days_ago: int = 0,
    like_count: int = 0,
) -> PersonaPost:
    return PersonaPost(
        id=days_ago + 1,
        persona_id=1,
        post_uri=f"at://test/post/{post_type}_{days_ago}",
        post_type=PostType(post_type),
        text=f"Test {post_type} from {days_ago} days ago",
        parent_text="Parent text" if post_type == "reply" else None,
        quoted_ref="at://quoted" if post_type == "quote" else None,
        langs=["en"],
        posted_at=datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days_ago),
        like_count=like_count,
        repost_count=0,
        reply_count=0,
        created_at=datetime.datetime.now(datetime.UTC),
    )


class TestContextSelector:
    def test_empty_posts(self) -> None:
        selector = ContextSelector(_settings())
        assert selector.select([]) == []

    def test_returns_up_to_count(self) -> None:
        selector = ContextSelector(_settings(context_posts_count=5))
        posts = [_post(days_ago=i) for i in range(20)]
        selected = selector.select(posts)
        assert len(selected) == 5

    def test_fewer_posts_than_count(self) -> None:
        selector = ContextSelector(_settings(context_posts_count=50))
        posts = [_post(days_ago=i) for i in range(3)]
        selected = selector.select(posts)
        assert len(selected) == 3

    def test_reply_weight_boost(self) -> None:
        """With 50% replies naturally, boost should push to 70% (the cap)."""
        selector = ContextSelector(_settings(context_posts_count=10))
        posts = [_post("post", days_ago=i) for i in range(10)]
        replies = [_post("reply", days_ago=i) for i in range(10)]
        all_posts = posts + replies

        selected = selector.select(all_posts)
        reply_count = sum(1 for p in selected if p.post_type == PostType.REPLY)
        # With natural ratio 0.5 + boost 0.2 = 0.7 (capped at 0.7)
        # So 7 replies out of 10
        assert reply_count == 7

    def test_no_replies_all_posts(self) -> None:
        """With no replies, all selections should be posts."""
        selector = ContextSelector(_settings(context_posts_count=5))
        posts = [_post("post", days_ago=i) for i in range(10)]
        selected = selector.select(posts)
        assert all(p.post_type != PostType.REPLY for p in selected)
        assert len(selected) == 5

    def test_floor_guarantees_some_replies(self) -> None:
        """Even with very few replies, at least 3 should be included."""
        selector = ContextSelector(
            _settings(context_posts_count=10, reply_weight_boost=0.0, reply_weight_cap=0.7)
        )
        # 95% posts, 5% replies → natural ratio ~0.05, boost 0.0 → weight 0.05
        # round(10 * 0.05) = 0 → floor kicks in, includes at least 3
        posts = [_post("post", days_ago=i) for i in range(19)]
        replies = [_post("reply", days_ago=i) for i in range(1)]

        selected = selector.select(posts + replies)
        reply_count = sum(1 for p in selected if p.post_type == PostType.REPLY)
        # Floor is min(3, available_replies) = min(3, 1) = 1
        assert reply_count >= 1

    def test_sorted_by_recency(self) -> None:
        """Final selection should be sorted by posted_at descending."""
        selector = ContextSelector(_settings(context_posts_count=5))
        posts = [_post("post", days_ago=i) for i in range(10)]
        selected = selector.select(posts)

        for i in range(len(selected) - 1):
            assert selected[i].posted_at >= selected[i + 1].posted_at

    def test_quotes_treated_as_originals(self) -> None:
        """Quote posts should be in the 'originals' bucket, not replies."""
        selector = ContextSelector(_settings(context_posts_count=10))
        quotes = [_post("quote", days_ago=i) for i in range(5)]
        replies = [_post("reply", days_ago=i) for i in range(5)]
        all_posts = quotes + replies

        selected = selector.select(all_posts)
        # Should include both quotes and replies
        has_quote = any(p.post_type == PostType.QUOTE for p in selected)
        has_reply = any(p.post_type == PostType.REPLY for p in selected)
        assert has_quote
        assert has_reply

    def test_redistribution_when_no_replies(self) -> None:
        """When no replies exist, all slots go to originals."""
        selector = ContextSelector(_settings(context_posts_count=10))
        posts = [_post("post", days_ago=i) for i in range(20)]
        selected = selector.select(posts)
        assert len(selected) == 10
        assert all(p.post_type != PostType.REPLY for p in selected)


class TestFormatForPrompt:
    def test_basic_prompt_structure(self) -> None:
        """Prompt includes profile, posts section, and rules."""
        selector = ContextSelector(_settings())
        persona = Persona(
            handle="alice.bsky.social",
            display_name="Alice",
            bio="I post about cats.",
            status=PersonaStatus.READY,
        )
        posts = [_post("post", days_ago=1)]
        prompt = selector.format_for_prompt(persona, posts)

        assert "Alice" in prompt
        assert "@alice.bsky.social" in prompt
        assert "I post about cats." in prompt
        assert "## Example Posts and Replies" in prompt
        assert "## Rules" in prompt

    def test_no_bio_fallback(self) -> None:
        """Missing bio shows placeholder text."""
        selector = ContextSelector(_settings())
        persona = Persona(
            handle="nobody.bsky.social",
            status=PersonaStatus.READY,
        )
        prompt = selector.format_for_prompt(persona, [])
        assert "(No bio available)" in prompt

    def test_pinned_post_included(self) -> None:
        """Pinned post text appears when set."""
        selector = ContextSelector(_settings())
        persona = Persona(
            handle="p.bsky.social",
            display_name="P",
            pinned_post_text="Read my manifesto here",
            status=PersonaStatus.READY,
        )
        prompt = selector.format_for_prompt(persona, [])
        assert "## Pinned Post" in prompt
        assert "Read my manifesto here" in prompt

    def test_pinned_post_absent(self) -> None:
        """No pinned post section when not set."""
        selector = ContextSelector(_settings())
        persona = Persona(
            handle="p.bsky.social",
            status=PersonaStatus.READY,
        )
        prompt = selector.format_for_prompt(persona, [])
        assert "## Pinned Post" not in prompt

    def test_reply_shows_parent_context(self) -> None:
        """Reply posts show parent text preview."""
        selector = ContextSelector(_settings())
        persona = Persona(handle="r.bsky.social", status=PersonaStatus.READY)
        posts = [_post("reply", days_ago=0)]
        prompt = selector.format_for_prompt(persona, posts)
        assert 'Replying to: "Parent text..."' in prompt

    def test_quote_shows_quoted_ref(self) -> None:
        """Quote posts show the quoted reference."""
        selector = ContextSelector(_settings())
        persona = Persona(handle="q.bsky.social", status=PersonaStatus.READY)
        posts = [_post("quote", days_ago=0)]
        prompt = selector.format_for_prompt(persona, posts)
        assert 'Re: "at://quoted..."' in prompt

    def test_display_name_fallback_to_handle(self) -> None:
        """Uses handle when display_name is None."""
        selector = ContextSelector(_settings())
        persona = Persona(
            handle="noname.bsky.social",
            display_name=None,
            status=PersonaStatus.READY,
        )
        prompt = selector.format_for_prompt(persona, [])
        assert "noname.bsky.social" in prompt.split("\n")[0]
