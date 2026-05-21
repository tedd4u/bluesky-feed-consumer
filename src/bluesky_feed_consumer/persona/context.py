"""Context selection algorithm for persona chat prompt injection."""

from __future__ import annotations

import random

from bluesky_feed_consumer.config import Settings
from bluesky_feed_consumer.models.chat import Persona, PersonaPost


class ContextSelector:
    """Selects posts for inclusion in the Claude system prompt.

    Algorithm:
    1. Separate posts into originals (post + quote) and replies
    2. Compute reply_weight = min(natural_ratio + boost, cap)
    3. Allocate: reply_count = round(count * reply_weight), post_count = rest
    4. For each category: 60% most recent, 40% random sample from older
    5. Floor: if replies exist but allocation is 0, include at least 3
    6. Sort final selection by posted_at descending
    """

    def __init__(self, settings: Settings) -> None:
        self.count = settings.context_posts_count
        self.reply_weight_boost = settings.reply_weight_boost
        self.reply_weight_cap = settings.reply_weight_cap
        self.recent_ratio = settings.recent_ratio

    def select(self, posts: list[PersonaPost]) -> list[PersonaPost]:
        """Select posts for the prompt context."""
        if not posts:
            return []

        originals = [p for p in posts if p.post_type != "reply"]
        replies = [p for p in posts if p.post_type == "reply"]

        # Compute adaptive reply weight
        total = len(originals) + len(replies)
        natural_ratio = len(replies) / total if total > 0 else 0.0
        reply_weight = min(natural_ratio + self.reply_weight_boost, self.reply_weight_cap)

        # Allocate counts
        reply_alloc = round(self.count * reply_weight)
        post_alloc = self.count - reply_alloc

        # Floor: if replies exist but got 0 allocation, give at least 3
        if replies and reply_alloc == 0:
            reply_alloc = min(3, len(replies))
            post_alloc = self.count - reply_alloc

        # Don't exceed what's available; redistribute slack
        reply_alloc = min(reply_alloc, len(replies))
        post_alloc = min(post_alloc, len(originals))

        shortfall = self.count - (reply_alloc + post_alloc)
        if shortfall > 0:
            post_alloc += min(shortfall, len(originals) - post_alloc)
            shortfall = self.count - (reply_alloc + post_alloc)
            reply_alloc += min(shortfall, len(replies) - reply_alloc)

        selected_originals = self._pick(originals, post_alloc)
        selected_replies = self._pick(replies, reply_alloc)

        combined = selected_originals + selected_replies
        combined.sort(key=lambda p: p.posted_at, reverse=True)
        return combined

    def _pick(self, posts: list[PersonaPost], count: int) -> list[PersonaPost]:
        """Pick from a list: recent_ratio most recent + rest sampled randomly."""
        if count <= 0 or not posts:
            return []

        # Sort by recency (most recent first)
        sorted_posts = sorted(posts, key=lambda p: p.posted_at, reverse=True)

        recent_count = max(1, round(count * self.recent_ratio))
        sample_count = count - recent_count

        recent = sorted_posts[:recent_count]
        remaining = sorted_posts[recent_count:]

        if sample_count > 0 and remaining:
            sample_count = min(sample_count, len(remaining))
            sampled = random.sample(remaining, sample_count)
        else:
            sampled = []

        return recent + sampled

    def format_for_prompt(self, persona: Persona, posts: list[PersonaPost]) -> str:
        """Format persona profile + selected posts into the system prompt."""
        lines: list[str] = []

        display = persona.display_name or persona.handle
        lines.append(f"You are roleplaying as {display} (@{persona.handle}) on Bluesky.")
        lines.append(
            "Respond exactly as this person would — matching their tone, "
            "vocabulary, opinions, and personality."
        )
        lines.append("")

        # Profile
        lines.append("## Profile")
        if persona.bio:
            lines.append(persona.bio)
        else:
            lines.append("(No bio available)")
        lines.append("")

        # Pinned post
        if persona.pinned_post_text:
            lines.append("## Pinned Post")
            lines.append(persona.pinned_post_text)
            lines.append("")

        # Posts
        lines.append("## Example Posts and Replies")
        for post in posts:
            time_str = post.posted_at.strftime("%a %I:%M%p")
            header = f"[{post.post_type} | {time_str} | {post.like_count} likes]"
            lines.append(header)

            if post.post_type == "reply" and post.parent_text:
                parent_preview = post.parent_text[:80]
                lines.append(f'  Replying to: "{parent_preview}..."')

            if post.post_type == "quote" and post.quoted_ref:
                lines.append(f'  Re: "{post.quoted_ref[:80]}..."')

            lines.append(post.text)
            lines.append("---")

        lines.append("")

        # Rules
        lines.append("## Rules")
        lines.append("- Stay in character. Never break character or acknowledge being AI.")
        lines.append(
            "- Match their writing style: sentence length, punctuation, "
            "emoji usage, capitalization."
        )
        lines.append("- Reference their known interests and opinions when relevant.")
        lines.append(
            "- Adapt response length to the conversation — short questions "
            "get short answers, thoughtful questions get thoughtful answers."
        )
        lines.append(
            "- If asked about something they haven't posted about, respond "
            "consistently with their personality."
        )

        return "\n".join(lines)
