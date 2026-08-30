"""Select only the glossary terms a single candidate can legitimately need."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from .glossary_audit import match_glossary_policies
from .models import (
    GlossaryPolicy,
    GlossaryRule,
    QaModelValidationError,
    RelevantGlossaryTerm,
)


_TRANSLATION_KEYS = ("rus", "translation", "target")
_POLICY_RANK = {
    GlossaryPolicy.MUST_TRANSLATE: 0,
    GlossaryPolicy.EITHER: 1,
    GlossaryPolicy.KEEP_ORIGINAL: 2,
}


@dataclass(frozen=True, slots=True)
class GlossaryTerm:
    """One book-level glossary entry with its authoritative translation policy."""

    original: str
    translation: str
    policy: GlossaryPolicy = GlossaryPolicy.MUST_TRANSLATE
    occurrences: int = 1

    def __post_init__(self) -> None:
        for field_name in ("original", "translation"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise QaModelValidationError(
                    f"glossary term {field_name} must be a nonempty string"
                )
        try:
            object.__setattr__(self, "policy", GlossaryPolicy(self.policy))
        except (TypeError, ValueError) as exc:
            raise QaModelValidationError("unsupported glossary policy") from exc
        if (
            self.policy is GlossaryPolicy.KEEP_ORIGINAL
            and self.translation != self.original
        ):
            raise QaModelValidationError(
                "KEEP_ORIGINAL glossary term must keep its original surface"
            )
        if isinstance(self.occurrences, bool) or not isinstance(self.occurrences, int):
            raise QaModelValidationError("glossary term occurrences must be an integer")
        if self.occurrences < 0:
            raise QaModelValidationError(
                "glossary term occurrences must be non-negative"
            )


@dataclass(frozen=True, slots=True)
class _Evidence:
    term: GlossaryTerm
    in_gap: bool
    exact_surface: bool
    occurrences: int

    @property
    def order_key(self) -> tuple[int, int, int, int, int, str]:
        return (
            0 if self.in_gap else 1,
            _POLICY_RANK[self.term.policy],
            0 if self.exact_surface else 1,
            -self.occurrences,
            -len(self.term.original),
            self.term.original,
        )


class GlossaryContextSelector:
    """Turn a whole-book glossary into the bounded subset one candidate justifies.

    Selection is evidence-driven: a term reaches the prompt only because it occurs
    in the candidate gap or its immediate context. Book-wide popularity may break
    ties between present terms but never introduces an absent one.
    """

    def select_for_candidate(
        self,
        glossary: Sequence[GlossaryTerm],
        source_text: str,
        source_context: str,
        max_terms: int,
    ) -> tuple[RelevantGlossaryTerm, ...]:
        """Return the ranked, deduplicated glossary subset relevant to one candidate."""
        terms = self._validated_terms(glossary)
        for value, field_name in ((source_text, "source_text"), (source_context, "source_context")):
            if not isinstance(value, str):
                raise TypeError(f"{field_name} must be a string")
        if isinstance(max_terms, bool) or not isinstance(max_terms, int):
            raise QaModelValidationError("max_terms must be an integer")
        if max_terms < 1:
            raise QaModelValidationError("max_terms must be positive")

        gap_counts = _match_counts(source_text, terms)
        context_counts = _match_counts(source_context, terms)
        evidence: list[_Evidence] = []
        for term in terms:
            in_gap_count = gap_counts.get(term.original, 0)
            in_context_count = context_counts.get(term.original, 0)
            total = in_gap_count + in_context_count
            if total < 1:
                continue
            evidence.append(
                _Evidence(
                    term=term,
                    in_gap=in_gap_count > 0,
                    exact_surface=(
                        term.original in source_text or term.original in source_context
                    ),
                    occurrences=total,
                )
            )

        evidence.sort(key=lambda item: item.order_key)
        return tuple(
            RelevantGlossaryTerm(
                original_term=item.term.original,
                canonical_translation=item.term.translation,
                policy=item.term.policy,
                occurrences=item.occurrences,
                priority=priority,
            )
            for priority, item in enumerate(evidence[:max_terms])
        )

    @staticmethod
    def _validated_terms(glossary: Sequence[GlossaryTerm]) -> tuple[GlossaryTerm, ...]:
        if isinstance(glossary, (str, bytes)) or not isinstance(glossary, Sequence):
            raise TypeError("glossary must be a sequence of GlossaryTerm values")
        seen: set[str] = set()
        terms: list[GlossaryTerm] = []
        for term in glossary:
            if not isinstance(term, GlossaryTerm):
                raise TypeError("glossary entries must be GlossaryTerm values")
            if term.original in seen:
                continue
            seen.add(term.original)
            terms.append(term)
        return tuple(terms)


def glossary_terms_from_project_entries(
    entries: Iterable[object],
) -> tuple[GlossaryTerm, ...]:
    """Map legacy project glossary records onto typed terms without inventing data.

    Legacy records carry no policy field. A record whose translation repeats the
    original is treated as KEEP_ORIGINAL because that is what the book already
    does; everything else keeps the default MUST_TRANSLATE.
    """

    terms: list[GlossaryTerm] = []
    seen: set[str] = set()
    for entry in entries or ():
        if not isinstance(entry, Mapping):
            continue
        original = str(entry.get("original") or "").strip()
        if not original or original in seen:
            continue
        translation = ""
        for key in _TRANSLATION_KEYS:
            translation = str(entry.get(key) or "").strip()
            if translation:
                break
        if not translation:
            continue
        policy = _explicit_policy(entry.get("qa_policy"))
        if policy is None:
            policy = (
                GlossaryPolicy.KEEP_ORIGINAL
                if translation == original
                else GlossaryPolicy.MUST_TRANSLATE
            )
        occurrences = entry.get("occurrences")
        seen.add(original)
        terms.append(
            GlossaryTerm(
                original=original,
                translation=translation,
                policy=policy,
                occurrences=(
                    occurrences
                    if isinstance(occurrences, int) and not isinstance(occurrences, bool)
                    and occurrences >= 0
                    else 1
                ),
            )
        )
    return tuple(terms)


def _explicit_policy(value: object) -> GlossaryPolicy | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return GlossaryPolicy(value.strip())
    except ValueError:
        return None


def _match_counts(text: str, terms: Sequence[GlossaryTerm]) -> dict[str, int]:
    if not text.strip() or not terms:
        return {}
    rules = tuple(GlossaryRule(term.original, term.policy) for term in terms)
    counts: dict[str, int] = {}
    for match in match_glossary_policies(text, rules):
        counts[match.term] = counts.get(match.term, 0) + 1
    return counts
