"""Shared text normalization for QA text comparisons.

`normalize_for_comparison` is the single canonical body for what used to be
six independently written `_normalize` functions across `gemini_translator.qa`
(addition_detector, foreign_text_filter, glossary_audit, llm/omission_repairer,
repair_validator, structural_repair). Four of those six were byte-identical;
addition_detector's copy had silently drifted to skip `.casefold()`, which
this module treats as the bug it is rather than a deliberate variant.

glossary_audit needs more than this: it folds curly quotes to ASCII, folds
"ё" to "е", and strips wrapping quote characters, because it compares
observed glossary translations rather than raw QA text. That extra folding
stays local to glossary_audit, built on top of `normalize_for_comparison`,
so the difference is explicit instead of another accidental drift.
"""

from __future__ import annotations

import re
import unicodedata

_WHITESPACE_RE = re.compile(r"\s+")


def normalize_for_comparison(value: str) -> str:
    """NFKC-normalize, collapse whitespace, and casefold for QA text comparisons."""
    return _WHITESPACE_RE.sub(" ", unicodedata.normalize("NFKC", value)).strip().casefold()
