"""Plain-text excerpt generation from Markdown."""

import re

EXCERPT_LENGTH = 200
# Only the start of the document can contribute to the excerpt; bounding the input keeps
# the regex work constant even for 1 MB notes.
_SCAN_CHARS = 8_000

_FENCE = re.compile(r"^(```|~~~).*?$", re.MULTILINE)
_HTML_TAG = re.compile(r"<[^>\n]+>")
_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_REF_LINK = re.compile(r"\[([^\]]*)\]\[[^\]]*\]")
_LINK_DEF = re.compile(r"^\s*\[[^\]]+\]:\s*\S+.*$", re.MULTILINE)
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s*", re.MULTILINE)
_SETEXT = re.compile(r"^\s*(=+|-+)\s*$", re.MULTILINE)
_BLOCKQUOTE = re.compile(r"^\s*(>\s?)+", re.MULTILINE)
_LIST_MARKER = re.compile(r"^\s*([-*+]|\d+[.)])\s+(\[[ xX]\]\s+)?", re.MULTILINE)
_HRULE = re.compile(r"^\s*([-*_]\s*){3,}$", re.MULTILINE)
_TABLE_PIPES = re.compile(r"\s*\|\s*")
_TABLE_ALIGN = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$", re.MULTILINE)
_EMPHASIS = re.compile(r"(\*\*|__|\*|_|~~)(?=\S)(.+?)(?<=\S)\1")
_INLINE_CODE = re.compile(r"`+([^`]*)`+")
_WHITESPACE = re.compile(r"\s+")


def markdown_to_plain(markdown: str) -> str:
    text = markdown[:_SCAN_CHARS]
    text = _FENCE.sub("", text)
    text = _HTML_TAG.sub(" ", text)
    text = _IMAGE.sub(r"\1", text)
    text = _LINK.sub(r"\1", text)
    text = _REF_LINK.sub(r"\1", text)
    text = _LINK_DEF.sub("", text)
    text = _TABLE_ALIGN.sub("", text)
    text = _HRULE.sub("", text)
    text = _HEADING.sub("", text)
    text = _SETEXT.sub("", text)
    text = _BLOCKQUOTE.sub("", text)
    text = _LIST_MARKER.sub("", text)
    text = _INLINE_CODE.sub(r"\1", text)
    for _ in range(2):  # nested emphasis such as ***bold italic***
        text = _EMPHASIS.sub(r"\2", text)
    text = _TABLE_PIPES.sub(" ", text)
    return _WHITESPACE.sub(" ", text).strip()


def make_excerpt(markdown: str, length: int = EXCERPT_LENGTH) -> str:
    """Plain-text excerpt of at most ``length`` characters, cut at a word boundary."""
    text = markdown_to_plain(markdown)
    if len(text) <= length:
        return text
    cut = text[: length - 1]
    space = cut.rfind(" ")
    if space > length // 2:
        cut = cut[:space]
    return cut.rstrip(" ,.;:-") + "…"
