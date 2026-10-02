import re
import uuid
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import AfterValidator, Field, StringConstraints, model_validator

from app.merge import normalize_newlines
from app.schemas.common import Schema, UserRef

MAX_TITLE_LENGTH = 200
MAX_CONTENT_BYTES = 1024 * 1024  # 1 MiB of UTF-8
MAX_TAGS = 20
MAX_TAG_LENGTH = 50


def validate_content(value: str) -> str:
    value = normalize_newlines(value)
    if len(value.encode("utf-8")) > MAX_CONTENT_BYTES:
        raise ValueError(f"content must be at most {MAX_CONTENT_BYTES} bytes (UTF-8)")
    return value


_TAG_PATTERN = re.compile(r"^[^\s,][^,]*$")


def normalize_tags(values: list[str]) -> list[str]:
    """Strip, lower-case, de-duplicate and sort; enforce the count and length limits."""
    tags: set[str] = set()
    for raw in values:
        tag = raw.strip().lower()
        if not tag:
            raise ValueError("tags must not be empty")
        if len(tag) > MAX_TAG_LENGTH:
            raise ValueError(f"tags must be at most {MAX_TAG_LENGTH} characters")
        if not _TAG_PATTERN.match(tag):
            raise ValueError("tags must not contain commas")
        tags.add(tag)
    if len(tags) > MAX_TAGS:
        raise ValueError(f"at most {MAX_TAGS} distinct tags are allowed")
    return sorted(tags)


Title = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_TITLE_LENGTH)
]
Content = Annotated[str, AfterValidator(validate_content)]
Tags = Annotated[list[str], Field(max_length=MAX_TAGS * 5), AfterValidator(normalize_tags)]


class NoteSort(StrEnum):
    UPDATED_AT = "updated_at"
    UPDATED_AT_DESC = "-updated_at"
    CREATED_AT = "created_at"
    CREATED_AT_DESC = "-created_at"
    TITLE = "title"
    TITLE_DESC = "-title"


class NoteCreate(Schema):
    team_id: uuid.UUID
    title: Title
    content: Content = ""
    tags: Tags = Field(default_factory=list)


class NoteUpdate(Schema):
    """Partial update. Omitted fields are left unchanged; ``null`` is not accepted."""

    title: Title | None = None
    content: Content | None = None
    tags: Tags | None = None

    @model_validator(mode="after")
    def _check_fields(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("provide at least one of title, content, tags")
        nulls = [name for name in self.model_fields_set if getattr(self, name) is None]
        if nulls:
            raise ValueError(f"fields may be omitted but not null: {', '.join(sorted(nulls))}")
        return self


class NoteSummary(Schema):
    """List representation. Deliberately has no ``content``."""

    id: uuid.UUID
    team_id: uuid.UUID
    title: str
    excerpt: str
    tags: list[str]
    version: int
    updated_at: datetime
    updated_by: UserRef
    deleted_at: datetime | None = None


class NoteRead(Schema):
    id: uuid.UUID
    team_id: uuid.UUID
    title: str
    content: str
    excerpt: str
    tags: list[str]
    version: int
    created_at: datetime
    updated_at: datetime
    created_by: UserRef
    updated_by: UserRef
    deleted_at: datetime | None
    deleted_by: UserRef | None


class NoteUpdateResponse(NoteRead):
    merged_from_versions: list[int] | None = Field(
        default=None,
        description="Set when the edit was auto-merged: `[base, current]` versions that were "
        "merged. Clients should refresh their view from this response.",
    )


class RevisionSummary(Schema):
    version: int
    title: str
    author: UserRef
    created_at: datetime


class RevisionRead(Schema):
    note_id: uuid.UUID
    version: int
    title: str
    content: str
    tags: list[str]
    author: UserRef
    created_at: datetime


# --- 409 edit conflict -----------------------------------------------------------------------


class LineRange(Schema):
    start: int = Field(description="First line (1-based, inclusive)")
    end: int = Field(description="Last line (inclusive); `start - 1` for an empty range")


class TitleConflictOut(Schema):
    field: Literal["title"] = "title"
    base: str
    theirs: str = Field(description="Value in the current (latest saved) version")
    yours: str = Field(description="Value in your request")


class ContentConflictOut(Schema):
    field: Literal["content"] = "content"
    base_lines: LineRange = Field(description="Location of the region in the base version")
    proposal_lines: LineRange = Field(
        description="Location of the marker block in `merged_proposal.content`"
    )
    base: str
    theirs: str = Field(description="Lines in the current (latest saved) version")
    yours: str = Field(description="Lines in your request")


ConflictOut = Annotated[TitleConflictOut | ContentConflictOut, Field(discriminator="field")]


class MergedProposal(Schema):
    title: str
    content: str = Field(description="Best-effort merge with `<<<<<<<`/`=======`/`>>>>>>>` markers")
    tags: list[str]


class EditConflictDetails(Schema):
    current: NoteRead
    merged_proposal: MergedProposal
    conflicts: list[ConflictOut]
    base_version: int


class EditConflictInfo(Schema):
    code: Literal["edit_conflict"] = "edit_conflict"
    message: str
    details: EditConflictDetails


class EditConflictResponse(Schema):
    """409 body: the standard error envelope with merge details."""

    error: EditConflictInfo


_USER_A = {"id": "0b7c6a1e-1f3e-4c55-9d43-8f1d2b3c4d5e", "display_name": "Alice"}
_USER_B = {"id": "5f0e8c2d-6a7b-4c8d-9e0f-1a2b3c4d5e6f", "display_name": "Bob"}
_NOTE_ID = "3fa85f64-5717-4562-b3fc-2c963f66afa6"
_TEAM_ID = "9c1d2e3f-4a5b-4c6d-8e7f-0a1b2c3d4e5f"

PATCH_MERGED_EXAMPLE: dict[str, Any] = {
    "id": _NOTE_ID,
    "team_id": _TEAM_ID,
    "title": "Release checklist",
    "content": "# Release\n- bump version (Bob)\n- tag release\n- publish notes (Alice)\n",
    "excerpt": "Release bump version (Bob) tag release publish notes (Alice)",
    "tags": ["ops", "release"],
    "version": 5,
    "created_at": "2026-10-01T09:00:00Z",
    "updated_at": "2026-10-02T10:15:00Z",
    "created_by": _USER_A,
    "updated_by": _USER_A,
    "deleted_at": None,
    "deleted_by": None,
    "merged_from_versions": [3, 4],
}

PATCH_CONFLICT_EXAMPLE: dict[str, Any] = {
    "error": {
        "code": "edit_conflict",
        "message": "Your edit conflicts with changes saved since version 3",
        "details": {
            "base_version": 3,
            "current": {
                **{k: v for k, v in PATCH_MERGED_EXAMPLE.items() if k != "merged_from_versions"},
                "content": "# Release\n- bump version to 2.0\n- tag release\n",
                "version": 4,
                "updated_by": _USER_B,
            },
            "merged_proposal": {
                "title": "Release checklist",
                "content": "# Release\n<<<<<<< v4 (Bob)\n- bump version to 2.0\n=======\n"
                "- bump version to 2.1\n>>>>>>> yours (based on v3)\n- tag release\n",
                "tags": ["ops", "release"],
            },
            "conflicts": [
                {
                    "field": "content",
                    "base_lines": {"start": 2, "end": 2},
                    "proposal_lines": {"start": 2, "end": 6},
                    "base": "- bump version\n",
                    "theirs": "- bump version to 2.0\n",
                    "yours": "- bump version to 2.1\n",
                }
            ],
        },
    }
}
