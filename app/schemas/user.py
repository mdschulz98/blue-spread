import uuid
from datetime import datetime
from typing import Self

from pydantic import EmailStr, model_validator

from app.schemas.common import DisplayName, Password, Schema


class UserRead(Schema):
    id: uuid.UUID
    email: str
    display_name: str
    is_active: bool
    is_superuser: bool
    created_at: datetime
    updated_at: datetime


class UserRegister(Schema):
    email: EmailStr
    display_name: DisplayName
    password: Password


class UserCreate(UserRegister):
    """Superuser-only user creation."""

    is_active: bool = True
    is_superuser: bool = False


class UserUpdateMe(Schema):
    display_name: DisplayName | None = None
    current_password: str | None = None
    new_password: Password | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.display_name is None and self.new_password is None:
            raise ValueError("provide display_name and/or new_password")
        if self.new_password is not None and not self.current_password:
            raise ValueError("current_password is required to set new_password")
        return self
