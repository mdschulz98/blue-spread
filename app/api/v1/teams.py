import uuid

from fastapi import APIRouter, Response, status

from app.api.deps import CurrentUser, SessionDep, TeamAdmin, TeamMember
from app.core.errors import ForbiddenError
from app.models import TeamMembership, TeamRole
from app.schemas.common import error_responses
from app.schemas.team import (
    MemberCreate,
    MemberRead,
    MemberUpdate,
    TeamCreate,
    TeamRead,
    TeamUpdate,
)
from app.services import teams as team_service
from app.services.access import TeamAccess, resolve_team_access

router = APIRouter(prefix="/teams", tags=["teams"])


def _team_read(access: TeamAccess) -> TeamRead:
    team = access.team
    return TeamRead(
        id=team.id,
        name=team.name,
        description=team.description,
        created_at=team.created_at,
        updated_at=team.updated_at,
        my_role=access.role,
    )


def _member_read(membership: TeamMembership) -> MemberRead:
    return MemberRead(
        user_id=membership.user_id,
        email=membership.user.email,
        display_name=membership.user.display_name,
        role=membership.role,
        created_at=membership.created_at,
    )


@router.get("", summary="Teams visible to the current user", responses=error_responses(401))
async def list_teams(user: CurrentUser, session: SessionDep) -> list[TeamRead]:
    """Members see their teams; superusers see all teams."""
    return [
        _team_read(TeamAccess(team=team, user=user, role=role))
        for team, role in await team_service.list_teams(session, user)
    ]


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Create a team (the creator becomes its admin)",
    responses=error_responses(401, 409, 422),
)
async def create_team(
    body: TeamCreate, user: CurrentUser, session: SessionDep, response: Response
) -> TeamRead:
    access = await team_service.create_team(session, user, body)
    response.headers["Location"] = f"/api/v1/teams/{access.team.id}"
    return _team_read(access)


@router.get("/{team_id}", summary="Get a team", responses=error_responses(401, 404))
async def read_team(access: TeamMember) -> TeamRead:
    return _team_read(access)


@router.patch(
    "/{team_id}", summary="Update a team (admin)", responses=error_responses(401, 403, 404, 409)
)
async def update_team(body: TeamUpdate, access: TeamAdmin, session: SessionDep) -> TeamRead:
    return _team_read(await team_service.update_team(session, access, body))


@router.delete(
    "/{team_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a team (admin; only if it has no non-deleted notes)",
    responses=error_responses(401, 403, 404, 409),
)
async def delete_team(access: TeamAdmin, session: SessionDep) -> Response:
    await team_service.delete_team(session, access)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- members ---------------------------------------------------------------------------------


@router.get("/{team_id}/members", summary="List team members", responses=error_responses(401, 404))
async def list_members(access: TeamMember, session: SessionDep) -> list[MemberRead]:
    return [_member_read(m) for m in await team_service.list_members(session, access.team.id)]


@router.post(
    "/{team_id}/members",
    status_code=status.HTTP_201_CREATED,
    summary="Add a member by email (admin)",
    responses=error_responses(401, 403, 404, 409, 422),
)
async def add_member(
    body: MemberCreate, access: TeamAdmin, session: SessionDep, response: Response
) -> MemberRead:
    membership = await team_service.add_member(session, access, body)
    response.headers["Location"] = f"/api/v1/teams/{access.team.id}/members/{membership.user_id}"
    return _member_read(membership)


@router.patch(
    "/{team_id}/members/{user_id}",
    summary="Change a member's role (admin)",
    responses=error_responses(401, 403, 404, 409, 422),
)
async def update_member(
    user_id: uuid.UUID, body: MemberUpdate, access: TeamAdmin, session: SessionDep
) -> MemberRead:
    """Demoting the last admin is rejected with 409 `last_admin`."""
    return _member_read(await team_service.update_member_role(session, access, user_id, body.role))


@router.delete(
    "/{team_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a member (admin), or leave the team (any member, for yourself)",
    responses=error_responses(401, 403, 404, 409),
)
async def remove_member(
    team_id: uuid.UUID, user_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> Response:
    """Removing the last admin is rejected with 409 `last_admin`."""
    access = await resolve_team_access(session, user, team_id)
    if user_id != user.id and not access.can(TeamRole.ADMIN):
        raise ForbiddenError("This action requires the admin role in the team")
    await team_service.remove_member(session, access, user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
