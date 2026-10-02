from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import UnauthorizedError
from app.core.security import InvalidTokenError, TokenType, create_token, decode_token
from app.models import User
from app.schemas.auth import TokenPair
from app.services import users as user_service


def issue_tokens(settings: Settings, user: User) -> TokenPair:
    access, _ = create_token(settings, user.id, TokenType.ACCESS)
    refresh, _ = create_token(settings, user.id, TokenType.REFRESH)
    return TokenPair(
        access_token=access,
        refresh_token=refresh,
        expires_in=settings.access_token_expire_minutes * 60,
        refresh_expires_in=settings.refresh_token_expire_minutes * 60,
    )


async def login(session: AsyncSession, settings: Settings, email: str, password: str) -> TokenPair:
    user = await user_service.authenticate(session, email, password)
    if user is None:
        raise UnauthorizedError("Incorrect email or password", code="invalid_credentials")
    return issue_tokens(settings, user)


async def user_from_token(
    session: AsyncSession, settings: Settings, token: str, token_type: TokenType
) -> User:
    try:
        payload = decode_token(settings, token, token_type)
    except InvalidTokenError:
        raise UnauthorizedError("Invalid or expired token", code="invalid_token") from None
    user = await session.get(User, payload.subject)
    if user is None or not user.is_active:
        raise UnauthorizedError("Invalid or expired token", code="invalid_token")
    return user


async def refresh(session: AsyncSession, settings: Settings, refresh_token: str) -> TokenPair:
    user = await user_from_token(session, settings, refresh_token, TokenType.REFRESH)
    return issue_tokens(settings, user)
