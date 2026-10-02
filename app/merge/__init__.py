"""Three-way merge of concurrent note edits. Pure functions: no database or FastAPI imports."""

from app.merge.note import merge_note, merge_tags, merge_title
from app.merge.text import LineMerger, normalize_newlines
from app.merge.types import (
    Conflict,
    ContentConflict,
    MergeLabels,
    MergeResult,
    NoteChanges,
    NoteSnapshot,
    TextMerger,
    TextMergeResult,
    TitleConflict,
)

__all__ = [
    "Conflict",
    "ContentConflict",
    "LineMerger",
    "MergeLabels",
    "MergeResult",
    "NoteChanges",
    "NoteSnapshot",
    "TextMergeResult",
    "TextMerger",
    "TitleConflict",
    "merge_note",
    "merge_tags",
    "merge_title",
    "normalize_newlines",
]
