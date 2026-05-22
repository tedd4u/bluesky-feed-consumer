"""Background loop that fetches history for newly registered personas."""

from __future__ import annotations

import asyncio
import datetime
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bluesky_feed_consumer.config import Settings
from bluesky_feed_consumer.models.chat import Persona, PersonaPost, PersonaStatus, PostType
from bluesky_feed_consumer.persona.fetcher import FetchedPost, PersonaFetcher

logger = logging.getLogger(__name__)


async def run_persona_poll(
    fetcher: PersonaFetcher,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """Poll for personas in 'loading' state and fetch their history.

    Runs every ``persona_poll_interval`` seconds.
    """
    while True:
        try:
            await _poll_once(fetcher, session_factory, settings)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Error in persona poll loop")

        await asyncio.sleep(settings.persona_poll_interval)


async def _poll_once(
    fetcher: PersonaFetcher,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """Check for loading personas and process them."""
    async with session_factory() as session:
        result = await session.execute(
            select(Persona).where(Persona.status == PersonaStatus.LOADING)
        )
        loading_personas = result.scalars().all()

    for persona in loading_personas:
        await _process_persona(persona, fetcher, session_factory, settings)


async def _process_persona(
    persona: Persona,
    fetcher: PersonaFetcher,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """Fetch profile + posts for a single persona, update DB.

    Profile info is committed first so the client can show the avatar
    while posts are still loading.  Posts are persisted in page-sized
    batches (20 at a time) with ``total_posts`` updated after each
    batch, giving the client visible forward progress.
    """
    logger.info("Processing persona @%s (id=%d)", persona.handle, persona.id)

    try:
        # 1. Fetch and persist profile info immediately
        profile = await fetcher.fetch_profile(persona.handle)
        async with session_factory() as session:
            result = await session.execute(select(Persona).where(Persona.id == persona.id))
            db_persona = result.scalar_one()
            db_persona.did = profile.did
            db_persona.display_name = profile.display_name
            db_persona.bio = profile.bio
            db_persona.avatar_url = profile.avatar_url
            await session.commit()

        # 2. Fetch posts in pages, persisting each batch
        total_posts = 0
        total_replies = 0

        async for page in fetcher.fetch_posts_paginated(
            profile.did, settings.max_history_posts
        ):
            async with session_factory() as session:
                result = await session.execute(
                    select(Persona).where(Persona.id == persona.id)
                )
                db_persona = result.scalar_one()

                for fp in page:
                    existing = await session.execute(
                        select(PersonaPost).where(PersonaPost.post_uri == fp.uri)
                    )
                    if existing.scalar_one_or_none() is None:
                        session.add(_fetched_to_model(fp, db_persona.id))
                        if fp.post_type == "reply":
                            total_replies += 1
                        else:
                            total_posts += 1

                db_persona.total_posts = total_posts + total_replies
                db_persona.total_replies = total_replies
                db_persona.last_corpus_update = datetime.datetime.now(datetime.UTC)
                await session.commit()

            logger.debug(
                "Persona @%s progress: %d posts, %d replies",
                persona.handle,
                total_posts,
                total_replies,
            )

        # 3. Mark ready
        async with session_factory() as session:
            result = await session.execute(select(Persona).where(Persona.id == persona.id))
            db_persona = result.scalar_one()
            db_persona.status = PersonaStatus.READY
            db_persona.error_message = None
            await session.commit()

        logger.info(
            "Persona @%s ready: %d posts, %d replies",
            persona.handle,
            total_posts,
            total_replies,
        )

    except Exception as exc:
        logger.exception("Failed to process persona @%s", persona.handle)
        async with session_factory() as session:
            result = await session.execute(select(Persona).where(Persona.id == persona.id))
            db_persona = result.scalar_one()
            db_persona.status = PersonaStatus.ERROR
            db_persona.error_message = str(exc)[:500]
            await session.commit()


def _fetched_to_model(fp: FetchedPost, persona_id: int) -> PersonaPost:
    """Convert a FetchedPost to a PersonaPost ORM model."""
    return PersonaPost(
        persona_id=persona_id,
        post_uri=fp.uri,
        post_type=PostType(fp.post_type),
        text=fp.text,
        parent_text=fp.parent_text,
        quoted_ref=fp.quoted_ref,
        langs=fp.langs if fp.langs else None,
        posted_at=fp.posted_at,
        like_count=fp.like_count,
        repost_count=fp.repost_count,
        reply_count=fp.reply_count,
    )
