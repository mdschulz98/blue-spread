import uuid

from fastapi import APIRouter, Response, status

from app.api.deps import CurrentUser, SessionDep, Superuser
from app.schemas.common import error_responses
from app.schemas.user import UserCreate, UserRead, UserUpdateMe
from app.services import users as user_service

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", summary="The current user", responses=error_responses(401))
async def read_me(user: CurrentUser) -> UserRead:
    return UserRead.model_validate(user)


@router.patch(
    "/me",
    summary="Update display name and/or change password",
    responses=error_responses(400, 401, 422),
)
async def update_me(body: UserUpdateMe, user: CurrentUser, session: SessionDep) -> UserRead:
    """Changing the password requires `current_password`."""
    return UserRead.model_validate(await user_service.update_me(session, user, body))


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Create a user (superuser only)",
    responses=error_responses(401, 403, 409, 422),
)
async def create_user(
    body: UserCreate, _: Superuser, session: SessionDep, response: Response
) -> UserRead:
    user = await user_service.create_user(
        session,
        email=body.email,
        display_name=body.display_name,
        password=body.password,
        is_active=body.is_active,
        is_superuser=body.is_superuser,
    )
    response.headers["Location"] = f"/api/v1/users/{user.id}"
    return UserRead.model_validate(user)


@router.get(
    "/{user_id}", summary="Get a user (superuser only)", responses=error_responses(401, 403, 404)
)
async def read_user(user_id: uuid.UUID, _: Superuser, session: SessionDep) -> UserRead:
    return UserRead.model_validate(await user_service.get_user(session, user_id))
