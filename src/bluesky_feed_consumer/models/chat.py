import datetime
import enum

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Index, Integer, Text, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bluesky_feed_consumer.models.base import Base


class PersonaStatus(enum.StrEnum):
    LOADING = "loading"
    READY = "ready"
    ERROR = "error"


class PostType(enum.StrEnum):
    POST = "post"
    REPLY = "reply"
    QUOTE = "quote"


class MessageRole(enum.StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class Persona(Base):
    __tablename__ = "personas"
    __table_args__ = {"schema": "chat"}

    id: Mapped[int] = mapped_column(primary_key=True)
    handle: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    did: Mapped[str | None] = mapped_column(Text)
    display_name: Mapped[str | None] = mapped_column(Text)
    bio: Mapped[str | None] = mapped_column(Text)
    avatar_url: Mapped[str | None] = mapped_column(Text)
    pinned_post_uri: Mapped[str | None] = mapped_column(Text)
    pinned_post_text: Mapped[str | None] = mapped_column(Text)

    status: Mapped[PersonaStatus] = mapped_column(
        Enum(PersonaStatus, schema="chat", name="persona_status"),
        nullable=False,
        default=PersonaStatus.LOADING,
    )
    error_message: Mapped[str | None] = mapped_column(Text)
    total_posts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_replies: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_corpus_update: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    posts: Mapped[list["PersonaPost"]] = relationship(
        back_populates="persona", cascade="all, delete-orphan"
    )
    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="persona", cascade="all, delete-orphan"
    )


class PersonaPost(Base):
    __tablename__ = "persona_posts"
    __table_args__ = (
        Index("idx_persona_posts_persona", "persona_id", "posted_at", postgresql_using="btree"),
        {"schema": "chat"},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    persona_id: Mapped[int] = mapped_column(
        ForeignKey("chat.personas.id", ondelete="CASCADE"), nullable=False
    )
    post_uri: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    post_type: Mapped[PostType] = mapped_column(
        Enum(PostType, schema="chat", name="post_type"), nullable=False
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    parent_text: Mapped[str | None] = mapped_column(Text)
    quoted_ref: Mapped[str | None] = mapped_column(Text)
    langs: Mapped[list[str] | None] = mapped_column(ARRAY(Text).with_variant(JSON, "sqlite"))
    posted_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    like_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    repost_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reply_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    persona: Mapped["Persona"] = relationship(back_populates="posts")


class ChatMessage(Base):
    __tablename__ = "messages"
    __table_args__ = (
        Index("idx_messages_persona", "persona_id", "created_at", postgresql_using="btree"),
        {"schema": "chat"},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    persona_id: Mapped[int] = mapped_column(
        ForeignKey("chat.personas.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[MessageRole] = mapped_column(
        Enum(MessageRole, schema="chat", name="message_role"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    persona: Mapped["Persona"] = relationship(back_populates="messages")
