"""Line-based three-way text merge, implemented on top of the ``merge3`` package."""

from merge3 import Merge3

from app.merge.types import ContentConflict, MergeLabels, TextMergeResult

START_MARKER = "<<<<<<<"
MID_MARKER = "======="
END_MARKER = ">>>>>>>"


def normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _split_lines(text: str) -> list[str]:
    """Split into lines that all end with ``\\n`` (the final line gets one if missing).

    ``str.splitlines`` is avoided on purpose: it also splits on form feeds, U+2028, etc.
    """
    if not text:
        return []
    if not text.endswith("\n"):
        text += "\n"
    return [line + "\n" for line in text[:-1].split("\n")]


def _merged_trailing_newline(base: str, theirs: str, yours: str) -> bool:
    """Three-way merge of the boolean "ends with a newline"."""
    base_nl, theirs_nl, yours_nl = (t.endswith("\n") for t in (base, theirs, yours))
    return yours_nl if yours_nl != base_nl else theirs_nl


class LineMerger:
    """Merges at line granularity. Edits to the same or adjacent lines conflict (like diff3)."""

    def merge(self, base: str, theirs: str, yours: str, labels: MergeLabels) -> TextMergeResult:
        base, theirs, yours = (normalize_newlines(t) for t in (base, theirs, yours))
        if theirs == yours or yours == base:
            return TextMergeResult(theirs)
        if theirs == base:
            return TextMergeResult(yours)

        base_lines, their_lines, your_lines = (_split_lines(t) for t in (base, theirs, yours))
        out: list[str] = []
        conflicts: list[ContentConflict] = []

        for region in Merge3(base_lines, their_lines, your_lines).merge_regions():
            kind = region[0]
            if kind == "unchanged":
                out.extend(base_lines[region[1] : region[2]])
            elif kind in ("same", "a"):
                out.extend(their_lines[region[1] : region[2]])
            elif kind == "b":
                out.extend(your_lines[region[1] : region[2]])
            elif kind == "conflict":
                _, z1, z2, a1, a2, b1, b2 = region  # type: ignore[misc]
                proposal_start = len(out) + 1
                out.append(f"{START_MARKER} {labels.theirs}\n")
                out.extend(their_lines[a1:a2])
                out.append(f"{MID_MARKER}\n")
                out.extend(your_lines[b1:b2])
                out.append(f"{END_MARKER} {labels.yours}\n")
                conflicts.append(
                    ContentConflict(
                        base_start=z1 + 1,
                        base_end=z2,
                        proposal_start=proposal_start,
                        proposal_end=len(out),
                        base="".join(base_lines[z1:z2]),
                        theirs="".join(their_lines[a1:a2]),
                        yours="".join(your_lines[b1:b2]),
                    )
                )
            else:  # pragma: no cover - merge3 only yields the kinds above
                raise ValueError(f"unexpected merge region {kind!r}")

        text = "".join(out)
        if text.endswith("\n") and not _merged_trailing_newline(base, theirs, yours):
            text = text[:-1]
        return TextMergeResult(text, tuple(conflicts))
