import uuid
from enum import StrEnum

from sqlalchemy import Enum, ForeignKey, Index, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.user import User


class TeamRole(StrEnum):
    VIEWER = "viewer"
    EDITOR = "editor"
    ADMIN = "admin"

    @property
    def rank(self) -> int:
        return _ROLE_RANK[self]

    def at_least(self, other: "TeamRole") -> bool:
        return self.rank >= other.rank


_ROLE_RANK = {TeamRole.VIEWER: 0, TeamRole.EDITOR: 1, TeamRole.ADMIN: 2}


class Team(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "teams"
    __table_args__ = (Index("uq_teams_name_lower", func.lower(text("name")), unique=True),)

    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text, default="", server_default="")

    def __repr__(self) -> str:
        return f"<Team {self.id} {self.name!r}>"


class TeamMembership(TimestampMixin, Base):
    __tablename__ = "team_memberships"
    __table_args__ = (Index("ix_team_memberships_user_id", "user_id"),)

    team_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[TeamRole] = mapped_column(
        Enum(TeamRole, name="team_role", values_callable=lambda e: [m.value for m in e])
    )

    user: Mapped[User] = relationship(lazy="raise")
