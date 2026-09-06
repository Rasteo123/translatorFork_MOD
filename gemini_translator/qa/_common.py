"""Shared, exception-neutral validation primitives for ``gemini_translator.qa``.

Deduplication note (pcluster-14): the same two tiny algorithms — "strip a
string and reject it if it ends up empty" and "clamp an int into a range,
falling back to a default for anything that isn't a plain int" — were
reimplemented independently under half a dozen names across this package
(``_nonempty_string``, ``_nonempty``, ``_identity``, ``_bounded_int``,
``_bounded``, ...), each wrapped in a different module's own typed exception,
and in one pair (``qa.settings._bounded_int`` vs
``qa.russian_nlp.slovnet_provider._bounded``) with the positional parameter
order silently swapped between copies.

This module holds the one algorithm each family actually shares. It never
raises a module-specific exception itself — callers that need one catch the
plain ``ValueError`` from ``validate_nonempty_string`` and re-raise their own
typed exception, so each caller's public behavior (exception type, message)
is unchanged by unification. ``bounded_int`` never raises; its keyword-only
signature is deliberate so a call site cannot silently swap ``minimum`` and
``maximum`` the way the two pre-refactor copies did.

Do NOT add the other members of pcluster-14 here: ``qa.llm.schemas``'s and
``qa.estimators.base``'s "nonempty" validators deliberately do not strip
their return value (schemas.py's ``_identifier`` depends on that to reject
identifiers with surrounding whitespace), and ``qa.models``'s
``_require_nonempty_string`` is a void validator with a different contract.
The ``_base_language`` family and the various ``_positive_int`` contracts are
likewise left where they are — see the pcluster-14 cluster report for why.
"""

from __future__ import annotations


def validate_nonempty_string(value: object, field_name: str) -> str:
    """Return ``value`` stripped, or raise ``ValueError`` if it is not a
    nonempty string once stripped.

    Common core of the strip-and-return family: ``qa.coverage_service``'s
    ``_nonempty_string``, ``qa.embeddings.base``'s ``_nonempty_string`` (and
    the ``qa.embeddings.cache`` names that are simply imports of it), and
    ``qa.repair_store``'s ``_identity``.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a nonempty string")
    return value.strip()


def bounded_int(value: object, *, default: int, minimum: int, maximum: int) -> int:
    """Clamp ``value`` into ``[minimum, maximum]``, falling back to ``default``
    for anything that is not a plain ``int`` (``bool`` included, since it is
    an ``int`` subclass but never a meaningful setting value here).

    Common core of ``qa.settings._bounded_int`` and
    ``qa.russian_nlp.slovnet_provider._bounded``, which had identical bodies
    but disagreed on the order of ``default``/``minimum``/``maximum``. Every
    parameter but ``value`` is keyword-only so a call site cannot silently
    swap them the way the two pre-unification copies did.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return min(max(value, minimum), maximum)
