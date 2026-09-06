"""Pure, pandas-free glossary term matching, shared by every QA hot path.

This module holds the three glossary functions that never needed pandas in
the first place: ``match_glossary_policies``, ``contains_term_forms`` and
``glossary_violation_reason``. They used to live in ``glossary_audit.py``
alongside ``GlossaryAuditor`` (which does need pandas for its DataFrame-based
conflict analysis), so importing any of them dragged pandas into every
process that only ever wanted a literal/lemma match check -- most of the QA
package, on every application start. Splitting them out here lets
``foreign_text_filter``, ``glossary_context``, ``language_validation``,
``repair_validator`` and ``structural_repair`` import just this module and
stay pandas-free.

``glossary_audit`` re-exports everything below for backward compatibility.
"""

from __future__ import annotations

from collections.abc import Iterable
import re
import unicodedata

from .models import (
    GlossaryPolicy,
    GlossaryPolicyMatch,
    GlossaryRule,
    RelevantGlossaryTerm,
)
from .text_normalize import normalize_for_comparison


_WORD_RE = re.compile(r"[^\W_]+", flags=re.UNICODE)
_QUOTE_TRANSLATION = str.maketrans({
    "«": '"', "»": '"', "„": '"', "“": '"', "”": '"', "‟": '"',
    "‹": "'", "›": "'", "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "ʼ": "'", "′": "'", "‵": "'", "＇": "'",
})
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def match_glossary_policies(
    text: str, rules: Iterable[GlossaryRule]
) -> tuple[GlossaryPolicyMatch, ...]:
    """Return only literal NFKC/casefold glossary matches in stable longest-first order.

    This intentionally does not consult morphology or frequency evidence: an inferred
    lemma family is not an authoritative policy match.
    """

    if not isinstance(text, str):
        raise TypeError("text must be a string")
    normalized_text = _normalize_policy_text(text)
    matches: list[GlossaryPolicyMatch] = []
    seen: set[tuple[str, GlossaryPolicy, int, int]] = set()
    for rule in rules:
        if not isinstance(rule, GlossaryRule):
            raise TypeError("rules must contain GlossaryRule instances")
        normalized_term = _normalize_policy_text(rule.term)
        if not normalized_term:
            continue
        start = normalized_text.find(normalized_term)
        while start >= 0:
            end = start + len(normalized_term)
            if _policy_boundary_matches(normalized_text, normalized_term, start, end):
                key = (rule.term, rule.policy, start, end)
                if key not in seen:
                    matches.append(
                        GlossaryPolicyMatch(rule.term, rule.policy, start, end, True)
                    )
                    seen.add(key)
            start = normalized_text.find(normalized_term, start + 1)
    return tuple(
        sorted(
            matches,
            key=lambda match: (
                -(match.end - match.start),
                match.start,
                _normalize_policy_text(match.term),
                match.policy.value,
                match.term,
            ),
        )
    )


def _normalize_policy_text(value: str) -> str:
    translated = unicodedata.normalize("NFKC", value).translate(_QUOTE_TRANSLATION)
    return normalize_for_comparison(translated)


def _policy_boundary_matches(
    text: str, term: str, start: int, end: int
) -> bool:
    has_cjk = _CJK_RE.search(term) is not None
    has_non_cjk_alnum = any(
        character.isalnum() and _CJK_RE.fullmatch(character) is None
        for character in term
    )
    if has_cjk and not has_non_cjk_alnum:
        return True
    if has_cjk:
        left_ok = start == 0 or not _is_non_cjk_word_character(text[start - 1])
        right_ok = end == len(text) or not _is_non_cjk_word_character(text[end])
        return left_ok and right_ok
    left_requires_boundary = term[0].isalnum() and _CJK_RE.fullmatch(term[0]) is None
    right_requires_boundary = term[-1].isalnum() and _CJK_RE.fullmatch(term[-1]) is None
    left_ok = not left_requires_boundary or start == 0 or not (
        text[start - 1].isalnum() or text[start - 1] == "_"
    )
    right_ok = not right_requires_boundary or end == len(text) or not (
        text[end].isalnum() or text[end] == "_"
    )
    return left_ok and right_ok


def _is_non_cjk_word_character(character: str) -> bool:
    return character == "_" or (
        character.isalnum() and _CJK_RE.fullmatch(character) is None
    )


def contains_term_forms(text: str, term: str) -> bool:
    """Report whether ``term`` occurs in ``text`` in any inflected form.

    Literal matching is not enough here: a Russian glossary term almost always
    appears declined, and «предельных атрибутах» is the same term as «предельный
    атрибут».  Lemmas answer that exactly, so pymorphy is used when the
    application already has it loaded; the prefix rule below is the fallback for
    a build without it, and it is deliberately the weaker of the two.
    """

    if match_glossary_policies(text, (GlossaryRule(term, GlossaryPolicy.EITHER),)):
        return True
    term_words = _words(term)
    text_words = _words(text)
    if not term_words or len(text_words) < len(term_words):
        return False
    lemmatize = _lemmatizer()
    if lemmatize is not None:
        term_lemmas = tuple(lemmatize(word) for word in term_words)
        text_lemmas = tuple(lemmatize(word) for word in text_words)
        for start in range(len(text_lemmas) - len(term_lemmas) + 1):
            if text_lemmas[start : start + len(term_lemmas)] == term_lemmas:
                return True
    for start in range(len(text_words) - len(term_words) + 1):
        window = text_words[start : start + len(term_words)]
        if all(
            _same_word_form(expected, actual)
            for expected, actual in zip(term_words, window, strict=True)
        ):
            return True
    return False


_LEMMA_CACHE: dict[str, str] = {}
# Beyond this the cache stops being a cache and starts being a leak: one book's
# vocabulary fits comfortably, a runaway caller does not.
_LEMMA_CACHE_LIMIT = 20000


def _lemmatizer():
    """Return a cached word->lemma function, or None without pymorphy.

    The analyzer is the application's single shared instance, built lazily: a
    session that never checks a glossary term never pays for the dictionaries.
    """
    try:
        from gemini_translator.utils.morphology import get_morph_analyzer

        analyzer = get_morph_analyzer()
    except Exception:  # noqa: BLE001 - morphology is an optional accelerator
        return None
    if analyzer is None:
        return None

    def lemma(word: str) -> str:
        cached = _LEMMA_CACHE.get(word)
        if cached is not None:
            return cached
        try:
            parsed = analyzer.parse(word)
        except Exception:  # noqa: BLE001 - an unparsable word is its own lemma
            parsed = ()
        value = parsed[0].normal_form if parsed else word
        if len(_LEMMA_CACHE) < _LEMMA_CACHE_LIMIT:
            _LEMMA_CACHE[word] = value
        return value

    return lemma


def _same_word_form(expected: str, actual: str) -> bool:
    shorter, longer = sorted((expected, actual), key=len)
    if len(shorter) < 3:
        return shorter == longer
    return longer.startswith(shorter)


def _words(value: str) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFKC", value).casefold().replace("ё", "е")
    return tuple(re.findall(r"[^\W_]+", normalized, flags=re.UNICODE))


# A single character is a stroke of some other word, not a term: in a Chinese
# glossary 9% of the entries are one character long, and each of them matches
# inside unrelated compounds.  Demanding their canonical translation in a
# fragment rejects perfectly good repairs — measured live on a real chapter.
MIN_CANONICAL_TERM_CHARS = 2


def glossary_violation_reason(
    fragment: str, source_text: str, glossary: Iterable[RelevantGlossaryTerm]
) -> str:
    """Name how a repaired fragment breaks a glossary policy, or return "".

    Three ways, and the third is the one that mattered in practice: for a
    Chinese source the model almost never leaves the original characters in a
    Russian fragment, so checking only for the original term passed everything —
    including a fragment that translated a glossary term by some synonym of its
    own while every other chapter used the canonical wording.
    """
    relevant = tuple(
        term
        for term in glossary
        if term.policy in {GlossaryPolicy.MUST_TRANSLATE, GlossaryPolicy.KEEP_ORIGINAL}
    )
    if not relevant or not source_text.strip():
        return ""
    rules = tuple(GlossaryRule(term.original_term, term.policy) for term in relevant)
    in_source = {match.term for match in match_glossary_policies(source_text, rules)}
    in_fragment = {match.term for match in match_glossary_policies(fragment, rules)}
    for term in relevant:
        if term.original_term not in in_source:
            continue
        present = term.original_term in in_fragment
        if term.policy is GlossaryPolicy.KEEP_ORIGINAL:
            if not present:
                return "original_term_dropped"
            continue
        if present:
            return "original_term_kept"
        if not _carries_its_own_canon(term, in_source):
            continue
        canonical = term.canonical_translation.strip()
        if canonical and not contains_term_forms(fragment, canonical):
            return "canonical_term_missing"
    return ""


def _carries_its_own_canon(term: RelevantGlossaryTerm, matched: set[str]) -> bool:
    """Report whether this term's presence in the source is a term, not an artefact.

    Two ways it is not.  A one-character entry matches inside any compound that
    happens to use that character.  And a term wholly contained in another
    matched term is that longer term's substring — «Тан» inside «Тан Юань» is
    not a second concept the fragment must name separately.
    """
    original = term.original_term
    if len(original) < MIN_CANONICAL_TERM_CHARS:
        return False
    return not any(
        other != original and original in other for other in matched
    )
