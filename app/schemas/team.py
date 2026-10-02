import uuid
from datetime import datetime
from typing import Annotated, Self

from pydantic import EmailStr, StringConstraints, model_validator

from app.models.team import TeamRole
from app.schemas.common import Schema

TeamName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
TeamDescription = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]


class TeamCreate(Schema):
    name: TeamName
    description: TeamDescription = ""


class TeamUpdate(Schema):
    name: TeamName | None = None
    description: TeamDescription | None = None

    @model_validator(mode="after")
    def _not_empty(self) -> Self:
        if self.name is None and self.description is None:
            raise ValueError("provide at least one of name, description")
        return self


class TeamRead(Schema):
    id: uuid.UUID
    name: str
    description: str
    created_at: datetime
    updated_at: datetime
    my_role: TeamRole | None
    """The current user's role, or null for a superuser who is not a member."""


class MemberCreate(Schema):
    email: EmailStr
    role: TeamRole = TeamRole.VIEWER


class MemberUpdate(Schema):
    role: TeamRole


class MemberRead(Schema):
    user_id: uuid.UUID
    email: str
    display_name: str
    role: TeamRole
    created_at: datetime
