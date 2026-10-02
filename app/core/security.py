"""Password hashing (argon2 via pwdlib) and JWT creation/verification (PyJWT)."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

import jwt
from pwdlib import PasswordHash

from app.core.config import Settings

_password_hash = PasswordHash.recommended()
# Used to spend comparable time when a login names an unknown user (avoids a timing oracle).
_DUMMY_HASH = _password_hash.hash("dummy-password-for-timing")


def hash_password(password: str) -> str:
    return _password_hash.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    if password_hash is None:
        _password_hash.verify(password, _DUMMY_HASH)
        return False
    return _password_hash.verify(password, password_hash)


class TokenType(StrEnum):
    ACCESS = "access"
    REFRESH = "refresh"


class InvalidTokenError(Exception):
    """Raised for any token that is malformed, expired, of the wrong type, or badly signed."""


@dataclass(frozen=True, slots=True)
class TokenPayload:
    subject: uuid.UUID
    token_type: TokenType
    expires_at: datetime


def create_token(
    settings: Settings,
    subject: uuid.UUID,
    token_type: TokenType,
    *,
    now: datetime | None = None,
) -> tuple[str, datetime]:
    issued_at = now or datetime.now(UTC)
    minutes = (
        settings.access_token_expire_minutes
        if token_type is TokenType.ACCESS
        else settings.refresh_token_expire_minutes
    )
    expires_at = issued_at + timedelta(minutes=minutes)
    claims = {
        "sub": str(subject),
        "typ": token_type.value,
        "iat": issued_at,
        "exp": expires_at,
        "jti": uuid.uuid4().hex,
    }
    token = jwt.encode(claims, settings.jwt_secret.get_secret_value(), settings.jwt_algorithm)
    return token, expires_at


def decode_token(settings: Settings, token: str, expected_type: TokenType) -> TokenPayload:
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
            options={"require": ["sub", "typ", "exp", "iat"]},
        )
        subject = uuid.UUID(str(claims["sub"]))
        token_type = TokenType(claims["typ"])
    except (jwt.PyJWTError, ValueError, KeyError) as exc:
        raise InvalidTokenError(str(exc)) from exc
    if token_type is not expected_type:
        raise InvalidTokenError(f"expected a {expected_type.value} token")
    return TokenPayload(
        subject=subject,
        token_type=token_type,
        expires_at=datetime.fromtimestamp(int(claims["exp"]), tz=UTC),
    )
