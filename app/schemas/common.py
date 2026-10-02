"""Shared field types and envelopes."""

import uuid
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

DisplayName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Password = Annotated[str, StringConstraints(min_length=8, max_length=128)]


class Schema(BaseModel):
    """Base for all API schemas: build from ORM objects/attributes, reject unknown fields."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class UserRef(Schema):
    id: uuid.UUID
    display_name: str


class Page[T](Schema):
    items: list[T]
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)


class ErrorInfo(Schema):
    code: str = Field(examples=["not_found"])
    message: str = Field(examples=["Note not found"])
    details: Any = None


class ErrorResponse(Schema):
    """Shape of every error response."""

    error: ErrorInfo


def error_responses(*status_codes: int) -> dict[int | str, dict[str, Any]]:
    """OpenAPI ``responses=`` entries documenting the error envelope for the given codes."""
    descriptions = {
        400: "Bad request",
        401: "Missing, invalid or expired access token",
        403: "Authenticated, but the role is insufficient for this action",
        404: "Not found, or not visible to the current user",
        409: "Conflict with existing data",
        412: "If-Match does not refer to a usable version",
        422: "Validation error",
        428: "If-Match header is required",
    }
    return {
        code: {"model": ErrorResponse, "description": descriptions.get(code, "Error")}
        for code in status_codes
    }
