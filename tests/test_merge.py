"""Unit tests of app.merge: pure functions, no database."""

import pytest

from app.merge import (
    ContentConflict,
    LineMerger,
    MergeLabels,
    NoteChanges,
    NoteSnapshot,
    TitleConflict,
    merge_note,
    merge_tags,
    merge_title,
)

BASE_TEXT = "line 1\nline 2\nline 3\nline 4\nline 5\n"
LABELS = MergeLabels(theirs="v2 (Bob)", yours="yours (based on v1)")


def merge_text(base: str, theirs: str, yours: str) -> tuple[str, tuple[ContentConflict, ...]]:
    result = LineMerger().merge(base, theirs, yours, LABELS)
    return result.text, result.conflicts


# --- content -----------------------------------------------------------------------------------


def test_non_overlapping_edits_merge_cleanly() -> None:
    theirs = BASE_TEXT.replace("line 1", "line 1 (theirs)")
    yours = BASE_TEXT.replace("line 5", "line 5 (yours)")
    text, conflicts = merge_text(BASE_TEXT, theirs, yours)
    assert conflicts == ()
    assert text == "line 1 (theirs)\nline 2\nline 3\nline 4\nline 5 (yours)\n"


def test_insertions_in_different_places_merge() -> None:
    theirs = "intro\n" + BASE_TEXT
    yours = BASE_TEXT + "outro\n"
    text, conflicts = merge_text(BASE_TEXT, theirs, yours)
    assert conflicts == ()
    assert text == "intro\n" + BASE_TEXT + "outro\n"


def test_identical_changes_on_both_sides_are_not_a_conflict() -> None:
    changed = BASE_TEXT.replace("line 3", "LINE 3")
    text, conflicts = merge_text(BASE_TEXT, changed, changed)
    assert conflicts == ()
    assert text == changed


def test_overlapping_edits_conflict_with_markers_and_details() -> None:
    theirs = BASE_TEXT.replace("line 3", "line 3 by them")
    yours = BASE_TEXT.replace("line 3", "line 3 by me")
    text, conflicts = merge_text(BASE_TEXT, theirs, yours)

    assert text == (
        "line 1\nline 2\n"
        "<<<<<<< v2 (Bob)\nline 3 by them\n=======\nline 3 by me\n>>>>>>> yours (based on v1)\n"
        "line 4\nline 5\n"
    )
    assert len(conflicts) == 1
    conflict = conflicts[0]
    assert (conflict.base_start, conflict.base_end) == (3, 3)
    assert (conflict.proposal_start, conflict.proposal_end) == (3, 7)
    assert conflict.base == "line 3\n"
    assert conflict.theirs == "line 3 by them\n"
    assert conflict.yours == "line 3 by me\n"
    lines = text.split("\n")
    assert lines[conflict.proposal_start - 1].startswith("<<<<<<<")
    assert lines[conflict.proposal_end - 1].startswith(">>>>>>>")


def test_multiple_conflicts_and_clean_regions_in_one_merge() -> None:
    base = "a\nb\nc\nd\ne\nf\ng\n"
    theirs = "A1\nb\nc\nD1\ne\nf\ng\n"
    yours = "A2\nb\nc\nd\ne\nf\nG2\n"
    text, conflicts = merge_text(base, theirs, yours)
    assert [(c.base_start, c.base_end) for c in conflicts] == [(1, 1)]
    assert "D1\n" in text
    assert "G2\n" in text


def test_crlf_is_normalized_before_merging() -> None:
    theirs = BASE_TEXT.replace("line 1", "line 1 (theirs)").replace("\n", "\r\n")
    yours = BASE_TEXT.replace("line 5", "line 5 (yours)")
    text, conflicts = merge_text(BASE_TEXT.replace("\n", "\r\n"), theirs, yours)
    assert conflicts == ()
    assert "\r" not in text
    assert text == "line 1 (theirs)\nline 2\nline 3\nline 4\nline 5 (yours)\n"


def test_missing_trailing_newline_does_not_cause_spurious_conflict() -> None:
    base = "a\nb\nc"
    theirs = "A\nb\nc"
    yours = "a\nb\nc\nd"
    text, conflicts = merge_text(base, theirs, yours)
    assert conflicts == ()
    assert text == "A\nb\nc\nd"


def test_trailing_newline_change_is_merged_like_a_field() -> None:
    text, conflicts = merge_text("a\nb\nc", "A\nb\nc", "a\nb\nc\n")
    assert conflicts == ()
    assert text == "A\nb\nc\n"


@pytest.mark.parametrize(
    ("theirs", "yours", "expected"),
    [
        ("x\n", "a\n", "x\n"),  # only theirs changed
        ("a\n", "y\n", "y\n"),  # only yours changed
        ("", "", ""),
    ],
)
def test_one_sided_changes_fast_path(theirs: str, yours: str, expected: str) -> None:
    text, conflicts = merge_text("a\n", theirs, yours)
    assert conflicts == ()
    assert text == expected


def test_both_sides_insert_at_same_point_conflicts_with_empty_base_range() -> None:
    base = "a\nb\n"
    text, conflicts = merge_text(base, "a\nX\nb\n", "a\nY\nb\n")
    assert len(conflicts) == 1
    assert conflicts[0].base == ""
    assert conflicts[0].base_end == conflicts[0].base_start - 1
    assert "X\n=======\nY\n" in text


def test_form_feed_and_unicode_separators_are_not_line_breaks() -> None:
    base = "a\x0cb\u2028c\n"
    text, conflicts = merge_text(base, base + "theirs\n", "start\n" + base)
    assert conflicts == ()
    assert text == "start\n" + base + "theirs\n"


# --- title -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("base", "theirs", "yours", "expected"),
    [
        ("T", "T", None, "T"),
        ("T", "Theirs", None, "Theirs"),
        ("T", "T", "Yours", "Yours"),
        ("T", "Theirs", "T", "Theirs"),
        ("T", "Same", "Same", "Same"),
    ],
)
def test_title_merge_without_conflict(
    base: str, theirs: str, yours: str | None, expected: str
) -> None:
    assert merge_title(base, theirs, yours) == (expected, None)


def test_title_changed_differently_on_both_sides_conflicts() -> None:
    title, conflict = merge_title("T", "Theirs", "Yours")
    assert title == "Theirs"
    assert conflict == TitleConflict(base="T", theirs="Theirs", yours="Yours")


# --- tags --------------------------------------------------------------------------------------


def test_tag_merge_concurrent_add_and_remove() -> None:
    base = ("a", "b", "c")
    theirs = ("a", "b", "c", "theirs-added")  # they added a tag
    yours = ("a", "c", "mine")  # you removed b and added mine
    assert merge_tags(base, theirs, yours) == ("a", "c", "mine", "theirs-added")


def test_tag_merge_removal_wins_over_concurrent_keep() -> None:
    assert merge_tags(("a", "b"), ("a", "b"), ("a",)) == ("a",)


def test_tag_merge_their_removal_is_kept_when_you_did_not_touch_it() -> None:
    assert merge_tags(("a", "b"), ("a",), ("a", "b", "new")) == ("a", "new")


def test_tags_not_in_request_keep_current() -> None:
    assert merge_tags(("a",), ("a", "b"), None) == ("a", "b")


# --- whole note --------------------------------------------------------------------------------


def test_merge_note_only_counts_fields_present_in_request() -> None:
    base = NoteSnapshot(title="T", content=BASE_TEXT, tags=("x",))
    current = NoteSnapshot(
        title="New title", content=BASE_TEXT.replace("line 1", "L1"), tags=("x", "y")
    )
    incoming = NoteChanges(content=BASE_TEXT.replace("line 5", "L5"))
    result = merge_note(base, current, incoming, labels=LABELS)
    assert not result.has_conflicts
    assert result.title == "New title"
    assert result.tags == ("x", "y")
    assert result.content == "L1\nline 2\nline 3\nline 4\nL5\n"


def test_merge_note_collects_conflicts_from_all_fields() -> None:
    base = NoteSnapshot(title="T", content="a\n", tags=())
    current = NoteSnapshot(title="Theirs", content="b\n", tags=("t",))
    incoming = NoteChanges(title="Yours", content="c\n", tags=("y",))
    result = merge_note(base, current, incoming, labels=LABELS)
    assert result.has_conflicts
    assert [c.field for c in result.conflicts] == ["title", "content"]
    assert result.tags == ("t", "y")


def test_merge_note_accepts_alternative_text_merger() -> None:
    class AlwaysYours:
        def merge(self, base: str, theirs: str, yours: str, labels: MergeLabels):  # type: ignore[no-untyped-def]
            from app.merge import TextMergeResult

            return TextMergeResult(yours)

    base = NoteSnapshot(title="T", content="a\n", tags=())
    current = NoteSnapshot(title="T", content="b\n", tags=())
    result = merge_note(base, current, NoteChanges(content="c\n"), text_merger=AlwaysYours())
    assert result.content == "c\n"
