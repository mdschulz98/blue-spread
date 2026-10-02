import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.deps import (
    CurrentUser,
    IfMatchVersion,
    NoteIdPath,
    ReadableNote,
    SessionDep,
    format_etag,
)
from app.models import Note, NoteRevision
from app.schemas.common import Page, UserRef, error_responses
from app.schemas.note import (
    PATCH_CONFLICT_EXAMPLE,
    PATCH_MERGED_EXAMPLE,
    EditConflictResponse,
    NoteCreate,
    NoteRead,
    NoteSort,
    NoteSummary,
    NoteUpdate,
    NoteUpdateResponse,
    RevisionRead,
    RevisionSummary,
)
from app.services import notes as note_service
from app.services.access import resolve_team_access

router = APIRouter(prefix="/notes", tags=["notes"])

_ETAG_HEADER = {
    "ETag": {"description": 'Current version, e.g. `"4"`', "schema": {"type": "string"}}
}


def _with_etag(response: Response, note: Note) -> None:
    response.headers["ETag"] = format_etag(note.version)


def _revision_summary(revision: NoteRevision) -> RevisionSummary:
    return RevisionSummary(
        version=revision.version,
        title=revision.title,
        author=UserRef.model_validate(revision.author),
        created_at=revision.created_at,
    )


# --- list ------------------------------------------------------------------------------------


async def _list_query(
    team_id: Annotated[
        uuid.UUID | None, Query(description="Restrict to one team (default: all your teams)")
    ] = None,
    q: Annotated[
        str | None,
        Query(
            max_length=500,
            description="Full-text search (`websearch_to_tsquery` syntax: `foo bar`, "
            '`"exact phrase"`, `-excluded`, `a or b`). Results are ordered by rank.',
        ),
    ] = None,
    tag: Annotated[
        list[str] | None,
        Query(max_length=20, description="Repeatable; notes must have all given tags"),
    ] = None,
    sort: Annotated[
        NoteSort, Query(description="Sort key; prefix `-` for descending")
    ] = NoteSort.UPDATED_AT_DESC,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    include_deleted: Annotated[
        bool,
        Query(description="Include soft-deleted notes (only from teams where you are editor+)"),
    ] = False,
) -> note_service.NoteListQuery:
    tags = tuple(sorted({t.strip().lower() for t in tag or [] if t.strip()}))
    return note_service.NoteListQuery(
        team_id=team_id,
        q=q.strip() if q and q.strip() else None,
        tags=tags,
        sort=sort,
        limit=limit,
        offset=offset,
        include_deleted=include_deleted,
    )


@router.get(
    "",
    summary="Browse and search notes (summaries only)",
    responses=error_responses(401, 403, 404, 422),
)
async def list_notes(
    query: Annotated[note_service.NoteListQuery, Depends(_list_query)],
    user: CurrentUser,
    session: SessionDep,
) -> Page[NoteSummary]:
    """Returns summaries — never the full `content`. Use `GET /notes/{id}` for that."""
    rows, total = await note_service.list_notes(session, user, query)
    items = [
        NoteSummary(
            id=row.id,
            team_id=row.team_id,
            title=row.title,
            excerpt=row.excerpt,
            tags=row.tags,
            version=row.version,
            updated_at=row.updated_at,
            deleted_at=row.deleted_at,
            updated_by=UserRef(id=row.updated_by_id, display_name=row.updated_by_name),
        )
        for row in rows
    ]
    return Page(items=items, total=total, limit=query.limit, offset=query.offset)


# --- create / read ---------------------------------------------------------------------------


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Create a note (editor+ in the target team)",
    responses={
        201: {"headers": _ETAG_HEADER},
        **error_responses(401, 403, 404, 422),
    },
)
async def create_note(
    body: NoteCreate, user: CurrentUser, session: SessionDep, response: Response
) -> NoteRead:
    access = await resolve_team_access(session, user, body.team_id)
    note = await note_service.create_note(session, user, access, body)
    response.headers["Location"] = f"/api/v1/notes/{note.id}"
    _with_etag(response, note)
    return NoteRead.model_validate(note)


@router.get(
    "/{note_id}",
    summary="Get a note, including content",
    responses={200: {"headers": _ETAG_HEADER}, **error_responses(401, 404)},
)
async def read_note(access: ReadableNote, response: Response) -> NoteRead:
    """The `ETag` header carries the version to send as `If-Match` with the next edit."""
    _with_etag(response, access.note)
    return NoteRead.model_validate(access.note)


# --- update ----------------------------------------------------------------------------------

_PATCH_DESCRIPTION = """
Partially update `title`, `content` and/or `tags`. **Requires `If-Match: "<version>"`** — the
version your edit is based on (the *base*, i.e. the `ETag` you got when you loaded the note).

* **base == current version** → the change is applied, `version` is incremented, `200`.
* **base < current version** (someone saved in between) → the server performs a **three-way
  merge** of *base*, *current* and *your* change, field by field. Only fields present in the
  request body count as changed by you.
  * `title`: taken from whichever side changed it; both changed it differently → conflict.
  * `tags`: `(current | added_by_you) - removed_by_you` (relative to base). Never conflicts.
  * `content`: line-based merge. Edits to separate regions merge cleanly; edits to the same or
    adjacent lines conflict.
  * Merged cleanly → saved as a new version; `200` with header `X-Note-Merged: true` and
    `merged_from_versions: [base, current]`. Refresh your editor from the response.
  * Any conflict → **nothing is saved**; `409` with `current` (send `current.version` as the
    next `If-Match`), `merged_proposal` (content with `<<<<<<<`/`=======`/`>>>>>>>` markers)
    and a structured `conflicts` list. Resolve and re-submit.
* **base > current version, or the base revision is unknown** → `412`.
* **Missing `If-Match`** → `428`.

If the result is identical to the current version, nothing is saved and no new version is
created.
"""


@router.patch(
    "/{note_id}",
    summary="Update a note (optimistic concurrency with automatic three-way merge)",
    description=_PATCH_DESCRIPTION,
    response_model=NoteUpdateResponse,
    responses={
        200: {
            "description": "Saved (fast-forward or merged)",
            "headers": {
                **_ETAG_HEADER,
                "X-Note-Merged": {
                    "description": "`true` when the edit was merged with concurrent changes",
                    "schema": {"type": "string", "enum": ["true"]},
                },
            },
            "content": {
                "application/json": {
                    "examples": {
                        "merged": {
                            "summary": "Edit based on v3 merged with v4",
                            "value": PATCH_MERGED_EXAMPLE,
                        }
                    }
                }
            },
        },
        409: {
            "model": EditConflictResponse,
            "description": "Conflicting concurrent edit; nothing was saved",
            "content": {"application/json": {"example": PATCH_CONFLICT_EXAMPLE}},
        },
        **error_responses(400, 401, 403, 404, 412, 422, 428),
    },
)
async def update_note(
    note_id: NoteIdPath,
    body: NoteUpdate,
    base_version: IfMatchVersion,
    user: CurrentUser,
    session: SessionDep,
    response: Response,
) -> Any:
    outcome = await note_service.update_note(session, user, note_id, base_version, body)
    _with_etag(response, outcome.note)
    result = NoteUpdateResponse.model_validate(outcome.note)
    if outcome.merged_from_versions is not None:
        response.headers["X-Note-Merged"] = "true"
        result.merged_from_versions = list(outcome.merged_from_versions)
    return result


# --- delete / restore ------------------------------------------------------------------------


@router.delete(
    "/{note_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-delete a note (editor+); requires If-Match",
    responses=error_responses(400, 401, 403, 404, 412, 428),
)
async def delete_note(
    note_id: NoteIdPath, base_version: IfMatchVersion, user: CurrentUser, session: SessionDep
) -> Response:
    """A stale `If-Match` is rejected with `412`: deletes are never merged."""
    await note_service.delete_note(session, user, note_id, base_version)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{note_id}/restore",
    summary="Restore a soft-deleted note (editor+)",
    responses={200: {"headers": _ETAG_HEADER}, **error_responses(401, 403, 404, 409)},
)
async def restore_note(
    note_id: NoteIdPath, user: CurrentUser, session: SessionDep, response: Response
) -> NoteRead:
    note = await note_service.restore_note(session, user, note_id)
    _with_etag(response, note)
    return NoteRead.model_validate(note)


# --- revisions -------------------------------------------------------------------------------


@router.get(
    "/{note_id}/revisions",
    summary="List revisions, newest first (no content)",
    responses=error_responses(401, 404),
)
async def list_revisions(
    access: ReadableNote,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[RevisionSummary]:
    revisions, total = await note_service.list_revisions(
        session, access.note, limit=limit, offset=offset
    )
    return Page(
        items=[_revision_summary(r) for r in revisions], total=total, limit=limit, offset=offset
    )


@router.get(
    "/{note_id}/revisions/{version}",
    summary="Get one revision, including content",
    responses=error_responses(401, 404),
)
async def read_revision(access: ReadableNote, version: int, session: SessionDep) -> RevisionRead:
    revision = await note_service.get_revision(session, access.note, version)
    return RevisionRead(
        note_id=revision.note_id,
        version=revision.version,
        title=revision.title,
        content=revision.content,
        tags=revision.tags,
        author=UserRef.model_validate(revision.author),
        created_at=revision.created_at,
    )
