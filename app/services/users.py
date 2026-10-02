import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError, ConflictError, NotFoundError
from app.core.security import hash_password, verify_password
from app.models import User
from app.schemas.user import UserUpdateMe


def normalize_email(email: str) -> str:
    return email.strip().lower()


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    stmt = select(User).where(func.lower(User.email) == normalize_email(email))
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_user(session: AsyncSession, user_id: uuid.UUID) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise NotFoundError("User not found")
    return user


async def create_user(
    session: AsyncSession,
    *,
    email: str,
    display_name: str,
    password: str,
    is_active: bool = True,
    is_superuser: bool = False,
) -> User:
    if await get_user_by_email(session, email) is not None:
        raise ConflictError("A user with this email already exists", code="email_taken")
    user = User(
        email=normalize_email(email),
        display_name=display_name,
        password_hash=hash_password(password),
        is_active=is_active,
        is_superuser=is_superuser,
    )
    session.add(user)
    await session.commit()
    return user


async def authenticate(session: AsyncSession, email: str, password: str) -> User | None:
    user = await get_user_by_email(session, email)
    if not verify_password(password, user.password_hash if user else None):
        return None
    if user is None or not user.is_active:
        return None
    return user


async def update_me(session: AsyncSession, user: User, data: UserUpdateMe) -> User:
    if data.new_password is not None:
        if not verify_password(data.current_password or "", user.password_hash):
            raise BadRequestError("Current password is incorrect", code="invalid_password")
        user.password_hash = hash_password(data.new_password)
    if data.display_name is not None:
        user.display_name = data.display_name
    await session.commit()
    return user
