"""Initial schema: stats and chat tables.

Revision ID: 0001
Revises:
Create Date: 2025-05-21

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ts = postgresql.TIMESTAMP(timezone=True)


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS stats")
    op.execute("CREATE SCHEMA IF NOT EXISTS chat")

    op.execute("""
        CREATE TYPE chat.persona_status AS ENUM ('loading', 'ready', 'error')
    """)
    op.execute("""
        CREATE TYPE chat.post_type AS ENUM ('post', 'reply', 'quote')
    """)
    op.execute("""
        CREATE TYPE chat.message_role AS ENUM ('user', 'assistant')
    """)

    now = sa.func.now()

    op.create_table(
        "snapshots",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("window_seconds", sa.Integer, nullable=False),
        sa.Column("window_start", _ts, nullable=False),
        sa.Column("post_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("user_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("like_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("repost_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("reply_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("prev_post_count", sa.Integer),
        sa.Column("prev_user_count", sa.Integer),
        sa.Column("prev_like_count", sa.Integer),
        sa.Column("prev_repost_count", sa.Integer),
        sa.Column("prev_reply_count", sa.Integer),
        sa.Column(
            "top_liked", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'")
        ),
        sa.Column(
            "top_reposted", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'")
        ),
        sa.Column(
            "language_breakdown",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("created_at", _ts, nullable=False, server_default=now),
        schema="stats",
    )
    op.create_index(
        "idx_snapshots_window",
        "snapshots",
        ["window_seconds", "window_start"],
        schema="stats",
        postgresql_using="btree",
    )

    op.create_table(
        "personas",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("handle", sa.Text, nullable=False, unique=True),
        sa.Column("did", sa.Text),
        sa.Column("display_name", sa.Text),
        sa.Column("bio", sa.Text),
        sa.Column("avatar_url", sa.Text),
        sa.Column("pinned_post_uri", sa.Text),
        sa.Column("pinned_post_text", sa.Text),
        sa.Column(
            "status",
            postgresql.ENUM(
                "loading",
                "ready",
                "error",
                name="persona_status",
                schema="chat",
                create_type=False,
            ),
            nullable=False,
            server_default="loading",
        ),
        sa.Column("error_message", sa.Text),
        sa.Column("total_posts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_replies", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_corpus_update", _ts),
        sa.Column("created_at", _ts, nullable=False, server_default=now),
        sa.Column("updated_at", _ts, nullable=False, server_default=now),
        schema="chat",
    )

    op.create_table(
        "persona_posts",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "persona_id",
            sa.BigInteger,
            sa.ForeignKey("chat.personas.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("post_uri", sa.Text, nullable=False, unique=True),
        sa.Column(
            "post_type",
            postgresql.ENUM(
                "post",
                "reply",
                "quote",
                name="post_type",
                schema="chat",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("parent_text", sa.Text),
        sa.Column("quoted_ref", sa.Text),
        sa.Column("langs", postgresql.ARRAY(sa.Text)),
        sa.Column("posted_at", _ts, nullable=False),
        sa.Column("like_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("repost_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("reply_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", _ts, nullable=False, server_default=now),
        schema="chat",
    )
    op.create_index(
        "idx_persona_posts_persona",
        "persona_posts",
        ["persona_id", "posted_at"],
        schema="chat",
        postgresql_using="btree",
    )

    op.create_table(
        "messages",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "persona_id",
            sa.BigInteger,
            sa.ForeignKey("chat.personas.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "role",
            postgresql.ENUM(
                "user",
                "assistant",
                name="message_role",
                schema="chat",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("created_at", _ts, nullable=False, server_default=now),
        schema="chat",
    )
    op.create_index(
        "idx_messages_persona",
        "messages",
        ["persona_id", "created_at"],
        schema="chat",
        postgresql_using="btree",
    )


def downgrade() -> None:
    op.drop_table("messages", schema="chat")
    op.drop_table("persona_posts", schema="chat")
    op.drop_table("personas", schema="chat")
    op.drop_table("snapshots", schema="stats")

    op.execute("DROP TYPE IF EXISTS chat.message_role")
    op.execute("DROP TYPE IF EXISTS chat.post_type")
    op.execute("DROP TYPE IF EXISTS chat.persona_status")

    op.execute("DROP SCHEMA IF EXISTS chat")
    op.execute("DROP SCHEMA IF EXISTS stats")
