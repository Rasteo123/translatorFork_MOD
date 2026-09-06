"""Shared low-level text/path sanitization primitives.

Extracted from the duplicated ``_safe_profile_segment`` helpers in
``gemini_translator/utils/settings.py`` and
``gemini_translator/api/handlers/workascii_chatgpt.py`` (pcluster-46). Those
two callers have different higher-level semantics (settings-profile alias
handling vs. browser-workspace naming) and remain thin wrappers around this
common primitive rather than being merged into one function.
"""


def sanitize_path_segment(value, *, strip_chars="_", max_length=None, default="segment") -> str:
    """Turn ``value`` into a safe single filesystem path segment.

    - Any character that is not alphanumeric, ``-`` or ``_`` is replaced
      with ``_`` (the input is whitespace-stripped first).
    - ``strip_chars`` (if truthy) is stripped from both ends of the result
      afterwards (e.g. ``"._-"`` to also drop leading/trailing dots).
    - ``max_length`` (if given) truncates the result after stripping.
    - If the result is empty, ``default`` is returned instead.
    """
    text = "".join(
        char if char.isalnum() or char in ("-", "_") else "_"
        for char in str(value or "").strip()
    )
    if strip_chars:
        text = text.strip(strip_chars)
    if max_length is not None:
        text = text[:max_length]
    return text or default
