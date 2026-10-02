"""Note lifecycle: create, list/search, versioned update with three-way merge, soft delete."""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import (
    ColumnElement,
    Row,
    and_,
    func,
    literal_column,
    or_,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, undefer

from app.core.errors import (
    ConflictError,
    NotFoundError,
    PreconditionFailedError,
    UnprocessableError,
)
from app.db.base import utcnow
from app.merge import MergeLabels, MergeResult, NoteChanges, NoteSnapshot, TitleConflict, merge_note
from app.models import Note, NoteRevision, TeamMembership, TeamRole, User
from app.models.note import SEARCH_CONFIG
from app.schemas.note import (
    MAX_CONTENT_BYTES,
    MAX_TAGS,
    ContentConflictOut,
    EditConflictDetails,
    LineRange,
    MergedProposal,
    NoteCreate,
    NoteRead,
    NoteSort,
    NoteUpdate,
    TitleConflictOut,
)
from app.services.access import TeamAccess, note_query, resolve_note_access, resolve_team_access
from app.services.text import make_excerpt

# --- create ----------------------------------------------------------------------------------


def _add_revision(session: AsyncSession, note: Note, author: User) -> None:
    session.add(
        NoteRevision(
            note_id=note.id,
            version=note.version,
            title=note.title,
            content=note.content,
            tags=list(note.tags),
            author_id=author.id,
        )
    )


async def create_note(
    session: AsyncSession, user: User, access: TeamAccess, data: NoteCreate
) -> Note:
    access.require(TeamRole.EDITOR)
    note = Note(
        team_id=access.team.id,
        title=data.title,
        content=data.content,
        tags=data.tags,
        excerpt=make_excerpt(data.content),
        version=1,
        created_by=user,
        updated_by=user,
        deleted_by=None,
    )
    session.add(note)
    await session.flush()
    _add_revision(session, note, user)
    await session.commit()
    return note


# --- list / search ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NoteListQuery:
    team_id: uuid.UUID | None = None
    q: str | None = None
    tags: tuple[str, ...] = ()
    sort: NoteSort = NoteSort.UPDATED_AT_DESC
    limit: int = 20
    offset: int = 0
    include_deleted: bool = False


_SORT_COLUMNS = {
    "updated_at": Note.updated_at,
    "created_at": Note.created_at,
    "title": func.lower(Note.title),
}


async def _visibility_conditions(
    session: AsyncSession, user: User, query: NoteListQuery
) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = []
    if query.team_id is not None:
        access = await resolve_team_access(session, user, query.team_id)
        if query.include_deleted:
            access.require(TeamRole.EDITOR)
        conditions.append(Note.team_id == query.team_id)
        if not query.include_deleted:
            conditions.append(Note.deleted_at.is_(None))
        return conditions

    if user.is_superuser:
        if not query.include_deleted:
            conditions.append(Note.deleted_at.is_(None))
        return conditions

    member_teams = select(TeamMembership.team_id).where(TeamMembership.user_id == user.id)
    conditions.append(Note.team_id.in_(member_teams))
    if query.include_deleted:
        # Deleted notes are only listed for teams in which the user is an editor or admin.
        editor_teams = member_teams.where(
            TeamMembership.role.in_([TeamRole.EDITOR, TeamRole.ADMIN])
        )
        conditions.append(or_(Note.deleted_at.is_(None), Note.team_id.in_(editor_teams)))
    else:
        conditions.append(Note.deleted_at.is_(None))
    return conditions


async def list_notes(
    session: AsyncSession, user: User, query: NoteListQuery
) -> tuple[Sequence[Row[*tuple[Any, ...]]], int]:
    """Return summary rows (never content) and the total number of matches."""
    conditions = await _visibility_conditions(session, user, query)
    if query.tags:
        conditions.append(Note.tags.contains(list(query.tags)))

    order_by: list[ColumnElement[Any]] = []
    if query.q:
        tsquery = func.websearch_to_tsquery(
            literal_column(f"'{SEARCH_CONFIG}'::regconfig"), query.q
        )
        conditions.append(Note.search_vector.op("@@")(tsquery))
        order_by.append(func.ts_rank_cd(Note.search_vector, tsquery).desc())

    descending = query.sort.value.startswith("-")
    sort_column = _SORT_COLUMNS[query.sort.value.lstrip("-")]
    order_by.append(sort_column.desc() if descending else sort_column.asc())
    order_by.append(Note.id.desc() if descending else Note.id.asc())

    total = await session.scalar(select(func.count()).select_from(Note).where(*conditions))

    stmt = (
        select(
            Note.id,
            Note.team_id,
            Note.title,
            Note.excerpt,
            Note.tags,
            Note.version,
            Note.updated_at,
            Note.deleted_at,
            User.id.label("updated_by_id"),
            User.display_name.label("updated_by_name"),
        )
        .join(User, User.id == Note.updated_by_id)
        .where(*conditions)
        .order_by(*order_by)
        .limit(query.limit)
        .offset(query.offset)
    )
    rows = (await session.execute(stmt)).all()
    return rows, int(total or 0)


# --- update with optimistic concurrency + three-way merge ------------------------------------


@dataclass(frozen=True, slots=True)
class UpdateOutcome:
    note: Note
    merged_from_versions: tuple[int, int] | None = None
    """``(base, current)`` when the edit was merged with concurrent changes."""


class EditConflictError(ConflictError):
    code = "edit_conflict"


def _snapshot(note: Note | NoteRevision) -> NoteSnapshot:
    return NoteSnapshot(title=note.title, content=note.content, tags=tuple(note.tags))


def _check_limits(result: MergeResult) -> None:
    if len(result.tags) > MAX_TAGS:
        raise UnprocessableError(
            f"The merged note would have more than {MAX_TAGS} tags", code="merge_limit_exceeded"
        )
    if len(result.content.encode("utf-8")) > MAX_CONTENT_BYTES:
        raise UnprocessableError(
            "The merged content would exceed the size limit", code="merge_limit_exceeded"
        )


def _conflict_details(note: Note, base_version: int, result: MergeResult) -> dict[str, Any]:
    conflicts: list[TitleConflictOut | ContentConflictOut] = []
    for conflict in result.conflicts:
        if isinstance(conflict, TitleConflict):
            conflicts.append(
                TitleConflictOut(base=conflict.base, theirs=conflict.theirs, yours=conflict.yours)
            )
        else:
            conflicts.append(
                ContentConflictOut(
                    base_lines=LineRange(start=conflict.base_start, end=conflict.base_end),
                    proposal_lines=LineRange(
                        start=conflict.proposal_start, end=conflict.proposal_end
                    ),
                    base=conflict.base,
                    theirs=conflict.theirs,
                    yours=conflict.yours,
                )
            )
    details = EditConflictDetails(
        base_version=base_version,
        current=NoteRead.model_validate(note),
        merged_proposal=MergedProposal(
            title=result.title, content=result.content, tags=list(result.tags)
        ),
        conflicts=conflicts,
    )
    return details.model_dump(mode="json")


async def update_note(
    session: AsyncSession,
    user: User,
    note_id: uuid.UUID,
    base_version: int,
    data: NoteUpdate,
) -> UpdateOutcome:
    """Apply a PATCH whose edit was based on ``base_version``. See README "Concurrent edits"."""
    # Row lock: concurrent writers to the same note are serialized from here to commit.
    access = await resolve_note_access(
        session, user, note_id, min_role=TeamRole.EDITOR, for_update=True
    )
    note = access.note
    incoming = NoteChanges(
        title=data.title,
        content=data.content,
        tags=tuple(data.tags) if data.tags is not None else None,
    )

    if base_version > note.version or base_version < 1:
        raise PreconditionFailedError(
            f"Version {base_version} does not exist; the current version is {note.version}",
            details={"current_version": note.version},
        )

    merged_from: tuple[int, int] | None = None
    current = _snapshot(note)
    if base_version == note.version:
        result = MergeResult(
            title=incoming.title if incoming.title is not None else current.title,
            content=incoming.content if incoming.content is not None else current.content,
            tags=incoming.tags if incoming.tags is not None else current.tags,
        )
    else:
        base_revision = await session.scalar(
            select(NoteRevision)
            .options(undefer(NoteRevision.content))
            .where(NoteRevision.note_id == note.id, NoteRevision.version == base_version)
        )
        if base_revision is None:
            raise PreconditionFailedError(
                f"Base version {base_version} is not available",
                details={"current_version": note.version},
            )
        labels = MergeLabels(
            theirs=f"v{note.version} ({note.updated_by.display_name})",
            yours=f"yours ({user.display_name}, based on v{base_version})",
        )
        result = merge_note(_snapshot(base_revision), current, incoming, labels=labels)
        if result.has_conflicts:
            details = _conflict_details(note, base_version, result)
            await session.rollback()  # save nothing; release the row lock early
            raise EditConflictError(
                f"Your edit conflicts with changes saved since version {base_version}",
                details=details,
            )
        merged_from = (base_version, note.version)

    _check_limits(result)
    if result.as_snapshot() == current:
        # Nothing actually changed (e.g. the same edit was already saved): no new version.
        # commit() (not rollback(), which would expire the instance) just releases the lock.
        await session.commit()
        return UpdateOutcome(note=note, merged_from_versions=merged_from)

    note.title = result.title
    note.content = result.content
    note.tags = list(result.tags)
    note.excerpt = make_excerpt(result.content)
    note.version += 1
    note.updated_by = user
    _add_revision(session, note, user)
    await session.commit()
    return UpdateOutcome(note=note, merged_from_versions=merged_from)


# --- delete / restore ------------------------------------------------------------------------


async def delete_note(
    session: AsyncSession, user: User, note_id: uuid.UUID, base_version: int
) -> None:
    access = await resolve_note_access(
        session, user, note_id, min_role=TeamRole.EDITOR, with_content=False, for_update=True
    )
    note = access.note
    if base_version != note.version:
        raise PreconditionFailedError(
            f"The note has changed since version {base_version}; it is now at {note.version}",
            details={"current_version": note.version},
        )
    note.deleted_at = utcnow()
    note.deleted_by = user
    await session.commit()


async def restore_note(session: AsyncSession, user: User, note_id: uuid.UUID) -> Note:
    access = await resolve_note_access(
        session, user, note_id, min_role=TeamRole.EDITOR, include_deleted=True, for_update=True
    )
    note = access.note
    if note.deleted_at is None:
        raise ConflictError("The note is not deleted", code="not_deleted")
    note.deleted_at = None
    note.deleted_by = None
    await session.commit()
    return note


# --- revisions -------------------------------------------------------------------------------


async def list_revisions(
    session: AsyncSession, note: Note, *, limit: int, offset: int
) -> tuple[Sequence[NoteRevision], int]:
    total = await session.scalar(
        select(func.count()).select_from(NoteRevision).where(NoteRevision.note_id == note.id)
    )
    stmt = (
        select(NoteRevision)
        .options(selectinload(NoteRevision.author))
        .where(NoteRevision.note_id == note.id)
        .order_by(NoteRevision.version.desc())
        .limit(limit)
        .offset(offset)
    )
    return (await session.execute(stmt)).scalars().all(), int(total or 0)


async def get_revision(session: AsyncSession, note: Note, version: int) -> NoteRevision:
    stmt = (
        select(NoteRevision)
        .options(selectinload(NoteRevision.author), undefer(NoteRevision.content))
        .where(and_(NoteRevision.note_id == note.id, NoteRevision.version == version))
    )
    revision = (await session.execute(stmt)).scalar_one_or_none()
    if revision is None:
        raise NotFoundError("Revision not found")
    return revision


async def reload_note(session: AsyncSession, note_id: uuid.UUID) -> Note:
    """Fresh copy of a note with relationships loaded (used after state-changing calls)."""
    stmt = note_query().where(Note.id == note_id).execution_options(populate_existing=True)
    return (await session.execute(stmt)).scalar_one()
