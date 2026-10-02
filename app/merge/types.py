"""Typed inputs and outputs of the note merge. No database or web framework types here."""

from dataclasses import dataclass, field
from typing import Literal, Protocol


@dataclass(frozen=True, slots=True)
class NoteSnapshot:
    """The mergeable fields of a note at one version."""

    title: str
    content: str
    tags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class NoteChanges:
    """The incoming edit. ``None`` means the field was not part of the request (unchanged)."""

    title: str | None = None
    content: str | None = None
    tags: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class MergeLabels:
    """Labels written after the ``<<<<<<<`` / ``>>>>>>>`` conflict markers."""

    theirs: str = "current"
    yours: str = "yours"


@dataclass(frozen=True, slots=True)
class TitleConflict:
    base: str
    theirs: str
    yours: str
    field: Literal["title"] = "title"


@dataclass(frozen=True, slots=True)
class ContentConflict:
    """One conflicting region of the content.

    Line numbers are 1-based and inclusive. ``base_start``/``base_end`` locate the region in the
    base text (``base_end == base_start - 1`` when both sides inserted at the same point);
    ``proposal_start``/``proposal_end`` locate the marker block (from ``<<<<<<<`` to ``>>>>>>>``)
    in the merged proposal.
    """

    base_start: int
    base_end: int
    proposal_start: int
    proposal_end: int
    base: str
    theirs: str
    yours: str
    field: Literal["content"] = "content"


type Conflict = TitleConflict | ContentConflict


@dataclass(frozen=True, slots=True)
class TextMergeResult:
    text: str
    conflicts: tuple[ContentConflict, ...] = ()

    @property
    def has_conflicts(self) -> bool:
        return bool(self.conflicts)


class TextMerger(Protocol):
    """Three-way text merge strategy (line-based today; could be word-based later)."""

    def merge(self, base: str, theirs: str, yours: str, labels: MergeLabels) -> TextMergeResult:
        """Merge ``yours`` (incoming) and ``theirs`` (current) relative to ``base``."""
        ...


@dataclass(frozen=True, slots=True)
class MergeResult:
    title: str
    content: str
    tags: tuple[str, ...]
    conflicts: tuple[Conflict, ...] = field(default=())

    @property
    def has_conflicts(self) -> bool:
        return bool(self.conflicts)

    def as_snapshot(self) -> NoteSnapshot:
        return NoteSnapshot(title=self.title, content=self.content, tags=self.tags)
