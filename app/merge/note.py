"""Field-by-field three-way merge of a note."""

from app.merge.text import LineMerger
from app.merge.types import (
    Conflict,
    MergeLabels,
    MergeResult,
    NoteChanges,
    NoteSnapshot,
    TextMerger,
    TitleConflict,
)

DEFAULT_TEXT_MERGER: TextMerger = LineMerger()


def merge_title(base: str, theirs: str, yours: str | None) -> tuple[str, TitleConflict | None]:
    if yours is None or yours in (base, theirs):
        return theirs, None
    if theirs == base:
        return yours, None
    # Both sides changed it to different values. Propose the saved value and report the conflict.
    return theirs, TitleConflict(base=base, theirs=theirs, yours=yours)


def merge_tags(
    base: tuple[str, ...], theirs: tuple[str, ...], yours: tuple[str, ...] | None
) -> tuple[str, ...]:
    """``(theirs | added_by_yours) - removed_by_yours``, with additions/removals relative to base.
    Never conflicts. Returned sorted for a stable representation."""
    if yours is None:
        return theirs
    base_set, your_set = set(base), set(yours)
    added = your_set - base_set
    removed = base_set - your_set
    return tuple(sorted((set(theirs) | added) - removed))


def merge_note(
    base: NoteSnapshot,
    current: NoteSnapshot,
    incoming: NoteChanges,
    *,
    labels: MergeLabels | None = None,
    text_merger: TextMerger = DEFAULT_TEXT_MERGER,
) -> MergeResult:
    """Merge an edit based on ``base`` into ``current`` (the latest saved version).

    Only fields present in ``incoming`` count as changed by the incoming side.
    """
    conflicts: list[Conflict] = []

    title, title_conflict = merge_title(base.title, current.title, incoming.title)
    if title_conflict is not None:
        conflicts.append(title_conflict)

    tags = merge_tags(base.tags, current.tags, incoming.tags)

    content = current.content
    if incoming.content is not None:
        text_result = text_merger.merge(
            base.content, current.content, incoming.content, labels or MergeLabels()
        )
        content = text_result.text
        conflicts.extend(text_result.conflicts)

    return MergeResult(title=title, content=content, tags=tags, conflicts=tuple(conflicts))
