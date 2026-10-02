from typing import Literal

from app.schemas.common import Schema


class TokenPair(Schema):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 - not a secret
    expires_in: int
    """Access token lifetime in seconds."""
    refresh_expires_in: int


class RefreshRequest(Schema):
    refresh_token: str
