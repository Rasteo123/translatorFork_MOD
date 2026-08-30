"""Word-level highlighting for one before/after pair of short fragments."""

from __future__ import annotations

from difflib import SequenceMatcher
from html import escape
import re


REMOVED_STYLE = "background-color: rgba(220, 60, 60, 0.28); border-radius: 2px;"
ADDED_STYLE = "background-color: rgba(60, 165, 90, 0.30); border-radius: 2px;"
_TOKEN_RE = re.compile(r"\w+|\W", re.UNICODE)


def tokenize(text: str) -> list[str]:
    """Split into words and separators, so a diff lands on word boundaries."""
    return _TOKEN_RE.findall(str(text or ""))


def highlight_pair(before: str, after: str) -> tuple[str, str]:
    """Return both sides as HTML, marking only what actually differs.

    Colour is never the only signal: the surrounding report always names which
    line is the original and which is the replacement.
    """

    before_tokens = tokenize(before)
    after_tokens = tokenize(after)
    matcher = SequenceMatcher(None, before_tokens, after_tokens, autojunk=False)
    before_html: list[str] = []
    after_html: list[str] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        removed = escape("".join(before_tokens[i1:i2]))
        added = escape("".join(after_tokens[j1:j2]))
        if tag == "equal":
            before_html.append(removed)
            after_html.append(added)
            continue
        if removed:
            before_html.append(f"<span style=\"{REMOVED_STYLE}\">{removed}</span>")
        if added:
            after_html.append(f"<span style=\"{ADDED_STYLE}\">{added}</span>")
    return "".join(before_html), "".join(after_html)


def highlight_added(text: str) -> str:
    """Return one fragment marked entirely as added, for a pure insertion."""
    return f"<span style=\"{ADDED_STYLE}\">{escape(str(text or ''))}</span>"
