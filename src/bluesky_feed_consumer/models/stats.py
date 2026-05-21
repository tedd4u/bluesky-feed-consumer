import datetime

from sqlalchemy import JSON, DateTime, Index, Integer, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from bluesky_feed_consumer.models.base import Base


class StatSnapshot(Base):
    __tablename__ = "snapshots"
    __table_args__ = (
        Index("idx_snapshots_window", "window_seconds", "window_start", postgresql_using="btree"),
        {"schema": "stats"},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    window_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    window_start: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    post_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    user_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    like_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    repost_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reply_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    prev_post_count: Mapped[int | None] = mapped_column(Integer)
    prev_user_count: Mapped[int | None] = mapped_column(Integer)
    prev_like_count: Mapped[int | None] = mapped_column(Integer)
    prev_repost_count: Mapped[int | None] = mapped_column(Integer)
    prev_reply_count: Mapped[int | None] = mapped_column(Integer)

    top_liked: Mapped[dict[str, object]] = mapped_column(
        JSONB().with_variant(JSON, "sqlite"), nullable=False, server_default="'[]'"
    )
    top_reposted: Mapped[dict[str, object]] = mapped_column(
        JSONB().with_variant(JSON, "sqlite"), nullable=False, server_default="'[]'"
    )
    language_breakdown: Mapped[dict[str, object]] = mapped_column(
        JSONB().with_variant(JSON, "sqlite"), nullable=False, server_default="'{}'"
    )

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
