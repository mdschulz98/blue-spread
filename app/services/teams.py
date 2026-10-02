"""Team and membership management, including the "at least one admin" invariant."""

import uuid
from collections.abc import Sequence

from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import ConflictError, NotFoundError
from app.models import Note, Team, TeamMembership, TeamRole, User
from app.schemas.team import MemberCreate, TeamCreate, TeamUpdate
from app.services import users as user_service
from app.services.access import TeamAccess, resolve_team_access


async def list_teams(session: AsyncSession, user: User) -> list[tuple[Team, TeamRole | None]]:
    if user.is_superuser:
        stmt = (
            select(Team, TeamMembership.role)
            .outerjoin(
                TeamMembership,
                (TeamMembership.team_id == Team.id) & (TeamMembership.user_id == user.id),
            )
            .order_by(func.lower(Team.name))
        )
    else:
        stmt = (
            select(Team, TeamMembership.role)
            .join(TeamMembership, TeamMembership.team_id == Team.id)
            .where(TeamMembership.user_id == user.id)
            .order_by(func.lower(Team.name))
        )
    return [(team, role) for team, role in await session.execute(stmt)]


async def _ensure_name_free(
    session: AsyncSession, name: str, *, exclude: uuid.UUID | None = None
) -> None:
    stmt = select(exists().where(func.lower(Team.name) == name.lower()))
    if exclude is not None:
        stmt = select(exists().where(func.lower(Team.name) == name.lower(), Team.id != exclude))
    if await session.scalar(stmt):
        raise ConflictError("A team with this name already exists", code="team_name_taken")


async def create_team(session: AsyncSession, user: User, data: TeamCreate) -> TeamAccess:
    await _ensure_name_free(session, data.name)
    team = Team(name=data.name, description=data.description)
    session.add(team)
    await session.flush()
    session.add(TeamMembership(team_id=team.id, user_id=user.id, role=TeamRole.ADMIN))
    await session.commit()
    return TeamAccess(team=team, user=user, role=TeamRole.ADMIN)


async def update_team(session: AsyncSession, access: TeamAccess, data: TeamUpdate) -> TeamAccess:
    team = access.team
    if data.name is not None and data.name != team.name:
        await _ensure_name_free(session, data.name, exclude=team.id)
        team.name = data.name
    if data.description is not None:
        team.description = data.description
    await session.commit()
    return access


async def delete_team(session: AsyncSession, access: TeamAccess) -> None:
    has_live_notes = await session.scalar(
        select(exists().where(Note.team_id == access.team.id, Note.deleted_at.is_(None)))
    )
    if has_live_notes:
        raise ConflictError(
            "The team still has notes; delete or move them first", code="team_not_empty"
        )
    # Soft-deleted notes, their revisions and memberships are removed by ON DELETE CASCADE.
    await session.delete(access.team)
    await session.commit()


# --- memberships -----------------------------------------------------------------------------


async def list_members(session: AsyncSession, team_id: uuid.UUID) -> Sequence[TeamMembership]:
    stmt = (
        select(TeamMembership)
        .join(User, User.id == TeamMembership.user_id)
        .options(selectinload(TeamMembership.user))
        .where(TeamMembership.team_id == team_id)
        .order_by(func.lower(User.display_name), User.id)
    )
    return (await session.execute(stmt)).scalars().all()


async def _get_membership(
    session: AsyncSession, team_id: uuid.UUID, user_id: uuid.UUID
) -> TeamMembership:
    stmt = (
        select(TeamMembership)
        .options(selectinload(TeamMembership.user))
        .where(TeamMembership.team_id == team_id, TeamMembership.user_id == user_id)
        .with_for_update(of=TeamMembership)
    )
    membership = (await session.execute(stmt)).scalar_one_or_none()
    if membership is None:
        raise NotFoundError("Member not found")
    return membership


async def _lock_team(session: AsyncSession, access: TeamAccess) -> None:
    """Serialize membership changes per team so the last-admin check cannot race."""
    await resolve_team_access(session, access.user, access.team.id, for_update=True)


async def _admin_count(session: AsyncSession, team_id: uuid.UUID) -> int:
    stmt = select(func.count()).where(
        TeamMembership.team_id == team_id, TeamMembership.role == TeamRole.ADMIN
    )
    return int(await session.scalar(stmt) or 0)


async def add_member(
    session: AsyncSession, access: TeamAccess, data: MemberCreate
) -> TeamMembership:
    user = await user_service.get_user_by_email(session, data.email)
    if user is None:
        raise NotFoundError("No user with this email", code="user_not_found")
    existing = await session.get(TeamMembership, (access.team.id, user.id))
    if existing is not None:
        raise ConflictError("The user is already a member of this team", code="already_member")
    membership = TeamMembership(team_id=access.team.id, user_id=user.id, role=data.role)
    membership.user = user
    session.add(membership)
    await session.commit()
    return membership


async def update_member_role(
    session: AsyncSession, access: TeamAccess, user_id: uuid.UUID, role: TeamRole
) -> TeamMembership:
    await _lock_team(session, access)
    membership = await _get_membership(session, access.team.id, user_id)
    if (
        membership.role is TeamRole.ADMIN
        and role is not TeamRole.ADMIN
        and await _admin_count(session, access.team.id) <= 1
    ):
        raise ConflictError("A team must keep at least one admin", code="last_admin")
    membership.role = role
    await session.commit()
    return membership


async def remove_member(session: AsyncSession, access: TeamAccess, user_id: uuid.UUID) -> None:
    await _lock_team(session, access)
    membership = await _get_membership(session, access.team.id, user_id)
    if membership.role is TeamRole.ADMIN and await _admin_count(session, access.team.id) <= 1:
        raise ConflictError("A team must keep at least one admin", code="last_admin")
    await session.delete(membership)
    await session.commit()
