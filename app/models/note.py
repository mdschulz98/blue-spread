import uuid
from datetime import datetime

from sqlalchemy import (
    Computed,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, utcnow
from app.models.user import User

# Only the first 256 KiB of content are indexed: PostgreSQL caps a tsvector at 1 MB, and a
# full 1 MB note of unusual text could otherwise exceed it and make the write fail.
SEARCH_CONTENT_PREFIX_CHARS = 262_144
SEARCH_VECTOR_EXPRESSION = (
    "setweight(to_tsvector('english'::regconfig, coalesce(title, '')), 'A') || "
    "setweight(to_tsvector('english'::regconfig, "
    f"left(coalesce(content, ''), {SEARCH_CONTENT_PREFIX_CHARS})), 'B')"
)
SEARCH_CONFIG = "english"


class Note(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "notes"
    __table_args__ = (
        Index("ix_notes_team_id_updated_at", "team_id", "updated_at"),
        Index("ix_notes_tags", "tags", postgresql_using="gin"),
        Index("ix_notes_search_vector", "search_vector", postgresql_using="gin"),
    )

    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text, deferred=True, deferred_raiseload=True)
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(String(50)), default=list, server_default=text("'{}'::varchar[]")
    )
    excerpt: Mapped[str] = mapped_column(String(300), default="", server_default="")
    version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))

    created_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    updated_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    deleted_at: Mapped[datetime | None] = mapped_column(default=None)
    deleted_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), default=None)

    # Never loaded by the ORM; only used in WHERE / ORDER BY clauses.
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR, Computed(SEARCH_VECTOR_EXPRESSION, persisted=True), deferred=True
    )

    created_by: Mapped[User] = relationship(foreign_keys=[created_by_id], lazy="raise")
    updated_by: Mapped[User] = relationship(foreign_keys=[updated_by_id], lazy="raise")
    deleted_by: Mapped[User | None] = relationship(foreign_keys=[deleted_by_id], lazy="raise")

    def __repr__(self) -> str:
        return f"<Note {self.id} v{self.version} {self.title!r}>"


class NoteRevision(Base):
    """Immutable snapshot of a note at a given version (version 1 included)."""

    __tablename__ = "note_revisions"
    __table_args__ = (
        ForeignKeyConstraint(["note_id"], ["notes.id"], ondelete="CASCADE"),
        Index("ix_note_revisions_author_id", "author_id"),
    )

    note_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text, deferred=True, deferred_raiseload=True)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(50)))
    author_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow, server_default=func.now())

    author: Mapped[User] = relationship(lazy="raise")
