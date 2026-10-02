"""Resolving what a user may do with a team or note.

Visibility rule: a resource the user cannot see raises ``NotFoundError`` (404) so existence is
not leaked; ``ForbiddenError`` (403) is only raised when the user can see it but lacks the role.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import Select, and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, undefer

from app.core.errors import ForbiddenError, NotFoundError
from app.models import Note, Team, TeamMembership, TeamRole, User


@dataclass(frozen=True, slots=True)
class TeamAccess:
    team: Team
    user: User
    role: TeamRole | None
    """The user's membership role; ``None`` for a superuser who is not a member."""

    @property
    def effective_role(self) -> TeamRole:
        if self.user.is_superuser:
            return TeamRole.ADMIN
        assert self.role is not None  # noqa: S101 - guaranteed by resolve_team_access
        return self.role

    def can(self, role: TeamRole) -> bool:
        return self.effective_role.at_least(role)

    def require(self, role: TeamRole) -> None:
        if not self.can(role):
            raise ForbiddenError(f"This action requires the {role.value} role in the team")


@dataclass(frozen=True, slots=True)
class NoteAccess:
    note: Note
    team: TeamAccess


async def resolve_team_access(
    session: AsyncSession, user: User, team_id: uuid.UUID, *, for_update: bool = False
) -> TeamAccess:
    stmt = (
        select(Team, TeamMembership.role)
        .outerjoin(
            TeamMembership,
            and_(TeamMembership.team_id == Team.id, TeamMembership.user_id == user.id),
        )
        .where(Team.id == team_id)
    )
    if for_update:
        stmt = stmt.with_for_update(of=Team)
    row = (await session.execute(stmt)).one_or_none()
    if row is None:
        raise NotFoundError("Team not found")
    team, role = row
    if role is None and not user.is_superuser:
        raise NotFoundError("Team not found")
    return TeamAccess(team=team, user=user, role=role)


def note_query(*, with_content: bool = True, for_update: bool = False) -> Select[Note]:
    """``select(Note)`` with the user references (and optionally content) eagerly loaded."""
    stmt = select(Note).options(
        selectinload(Note.created_by),
        selectinload(Note.updated_by),
        selectinload(Note.deleted_by),
    )
    if with_content:
        stmt = stmt.options(undefer(Note.content))
    if for_update:
        stmt = stmt.with_for_update(of=Note).execution_options(populate_existing=True)
    return stmt


async def resolve_note_access(
    session: AsyncSession,
    user: User,
    note_id: uuid.UUID,
    *,
    min_role: TeamRole = TeamRole.VIEWER,
    include_deleted: bool = False,
    with_content: bool = True,
    for_update: bool = False,
) -> NoteAccess:
    """Load a note the user may access with ``min_role``.

    Deleted notes are only visible when ``include_deleted`` is set *and* the user is an editor+;
    for anyone else they do not exist (404).
    """
    stmt = note_query(with_content=with_content, for_update=for_update).where(Note.id == note_id)
    note = (await session.execute(stmt)).scalar_one_or_none()
    if note is None:
        raise NotFoundError("Note not found")
    try:
        team = await resolve_team_access(session, user, note.team_id)
    except NotFoundError:
        raise NotFoundError("Note not found") from None
    if note.deleted_at is not None and not (include_deleted and team.can(TeamRole.EDITOR)):
        raise NotFoundError("Note not found")
    team.require(min_role)
    return NoteAccess(note=note, team=team)
