from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from fastapi.security import OAuth2PasswordRequestForm

from app.api.deps import SessionDep, SettingsDep
from app.core.errors import ForbiddenError
from app.schemas.auth import RefreshRequest, TokenPair
from app.schemas.common import error_responses
from app.schemas.user import UserRead, UserRegister
from app.services import auth as auth_service
from app.services import users as user_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/login",
    summary="Log in with email and password (OAuth2 password form)",
    responses=error_responses(401, 422),
)
async def login(
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    session: SessionDep,
    settings: SettingsDep,
) -> TokenPair:
    """`username` is the user's email address."""
    return await auth_service.login(session, settings, form.username, form.password)


@router.post(
    "/refresh",
    summary="Exchange a refresh token for a new token pair",
    responses=error_responses(401, 422),
)
async def refresh(body: RefreshRequest, session: SessionDep, settings: SettingsDep) -> TokenPair:
    return await auth_service.refresh(session, settings, body.refresh_token)


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    summary="Self-service registration (only when ALLOW_REGISTRATION is enabled)",
    responses=error_responses(403, 409, 422),
)
async def register(
    body: UserRegister, session: SessionDep, settings: SettingsDep, response: Response
) -> UserRead:
    if not settings.registration_enabled:
        raise ForbiddenError("Registration is disabled", code="registration_disabled")
    user = await user_service.create_user(
        session, email=body.email, display_name=body.display_name, password=body.password
    )
    response.headers["Location"] = "/api/v1/users/me"
    return UserRead.model_validate(user)
