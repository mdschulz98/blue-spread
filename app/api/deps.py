"""Reusable dependencies: settings, DB session, current user, team/note access checks."""

import re
import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Header, Path, Query, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import BadRequestError, ForbiddenError, PreconditionRequiredError
from app.core.security import TokenType
from app.db.session import get_session
from app.models import TeamRole, User
from app.services import auth as auth_service
from app.services.access import NoteAccess, TeamAccess, resolve_note_access, resolve_team_access


def get_app_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


SettingsDep = Annotated[Settings, Depends(get_app_settings)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)], session: SessionDep, settings: SettingsDep
) -> User:
    return await auth_service.user_from_token(session, settings, token, TokenType.ACCESS)


CurrentUser = Annotated[User, Depends(get_current_user)]


async def require_superuser(user: CurrentUser) -> User:
    if not user.is_superuser:
        raise ForbiddenError("Superuser privileges required")
    return user


Superuser = Annotated[User, Depends(require_superuser)]


# --- teams -----------------------------------------------------------------------------------

TeamIdPath = Annotated[uuid.UUID, Path(description="Team ID")]


def require_team_role(role: TeamRole) -> Callable[..., Awaitable[TeamAccess]]:
    """Dependency factory: the path team, which the user must belong to with ``role`` or above.

    Non-members get 404; members with a lower role get 403.
    """

    async def dependency(team_id: TeamIdPath, user: CurrentUser, session: SessionDep) -> TeamAccess:
        access = await resolve_team_access(session, user, team_id)
        access.require(role)
        return access

    return dependency


TeamMember = Annotated[TeamAccess, Depends(require_team_role(TeamRole.VIEWER))]
TeamAdmin = Annotated[TeamAccess, Depends(require_team_role(TeamRole.ADMIN))]


# --- notes -----------------------------------------------------------------------------------

NoteIdPath = Annotated[uuid.UUID, Path(description="Note ID")]


async def get_readable_note(
    note_id: NoteIdPath,
    user: CurrentUser,
    session: SessionDep,
    include_deleted: Annotated[
        bool, Query(description="Return the note even if soft-deleted (editors and admins only)")
    ] = False,
) -> NoteAccess:
    return await resolve_note_access(session, user, note_id, include_deleted=include_deleted)


ReadableNote = Annotated[NoteAccess, Depends(get_readable_note)]


# --- conditional requests --------------------------------------------------------------------

_ETAG_RE = re.compile(r'^\s*(?:W/)?"?(\d{1,18})"?\s*$')


def format_etag(version: int) -> str:
    return f'"{version}"'


def parse_if_match(value: str | None) -> int:
    if value is None or not value.strip():
        raise PreconditionRequiredError(
            'Send If-Match: "<version>" with the version your edit is based on'
        )
    match = _ETAG_RE.match(value)
    if match is None:
        raise BadRequestError(
            'If-Match must be a single version ETag such as "3"', code="invalid_if_match"
        )
    return int(match.group(1))


async def get_if_match_version(
    if_match: Annotated[
        str | None,
        Header(
            alias="If-Match",
            description='ETag of the version the change is based on, e.g. `"3"`.',
        ),
    ] = None,
) -> int:
    return parse_if_match(if_match)


IfMatchVersion = Annotated[int, Depends(get_if_match_version)]
