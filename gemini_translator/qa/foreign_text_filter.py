"""Pure safety filter for intentional foreign text in semantic alignment gaps."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import re
import unicodedata

from .glossary_audit import match_glossary_policies
from .models import (
    AlignmentResult,
    CandidateContext,
    CandidateFilterResult,
    FilteredCandidate,
    ForeignTextDecision,
    GapCandidate,
    GlossaryPolicy,
    GlossaryPolicyMatch,
    GlossaryRule,
    QaModelValidationError,
)


_URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>]+", re.IGNORECASE)
_EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
_ISBN_RE = re.compile(r"ISBN(?:-1[03])?\s*:?[\s-]*(?:\d[\d\s-]{8,}[\dXx])", re.IGNORECASE)
_SKU_RE = re.compile(
    r"(?:SKU|article|item|арт(?:икул)?)\s*[:#.-]?\s*[A-Z0-9][A-Z0-9._/-]{2,}",
    re.IGNORECASE,
)
_CODE_RE = re.compile(r"(?=[A-Z0-9._/-]*\d)[A-Z0-9][A-Z0-9._/-]{3,}")
_LATIN_NAME_RE = re.compile(
    r"[A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’-]+(?:\s+[A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’-]+){1,3}"
)
_WRAPPER_CHARACTERS = " \t\r\n\"'`«»„“”‟‹›‘’‚‛ʼ—–-:;,.!?()[]{}"
_NON_NAME_LEADS = frozenset(
    {"a", "an", "do", "he", "i", "it", "not", "she", "the", "they", "we", "you"}
)
_PATTERNS = (
    ("url", _URL_RE),
    ("email", _EMAIL_RE),
    ("isbn", _ISBN_RE),
    ("sku", _SKU_RE),
    ("code", _CODE_RE),
)


class ForeignTextFilter:
    """Classify gaps without modifying text or granting repair authority."""

    def __init__(self, glossary: Iterable[GlossaryRule] = ()) -> None:
        self._glossary = tuple(glossary)
        if not all(isinstance(rule, GlossaryRule) for rule in self._glossary):
            raise QaModelValidationError("glossary must contain GlossaryRule instances")

    def classify(
        self, candidate: GapCandidate, context: CandidateContext
    ) -> ForeignTextDecision:
        if not isinstance(candidate, GapCandidate):
            raise QaModelValidationError("candidate must be a GapCandidate")
        if not isinstance(context, CandidateContext):
            raise QaModelValidationError("context must be a CandidateContext")
        if context.candidate_id != candidate.candidate_id:
            return _decision(
                "invalid_context",
                "report_only",
                "low",
                "candidate_context_id_mismatch",
            )
        if candidate.side == "target":
            return _decision(
                "target_addition",
                "report_only",
                "medium",
                "target_side_addition_is_not_an_omission",
            )
        if not context.source_text.strip():
            return _decision(
                "invalid_context",
                "report_only",
                "low",
                "candidate_source_text_missing",
            )

        glossary_decision = self._classify_glossary(context)
        if glossary_decision is not None:
            return glossary_decision

        protected_item = _protected_item(context.source_text)
        if protected_item is not None:
            category, whole = protected_item
            if whole:
                return _decision(
                    "protected_item",
                    "exclude",
                    "high",
                    f"whole_protected_item:{category}",
                )
            return self._semantic_or_report(
                candidate,
                f"protected_item_embedded_in_prose:{category}",
            )

        foreign_context = _foreign_context_evidence(context)
        if foreign_context is not None:
            return _decision(
                "intentional_foreign",
                "exclude",
                "high",
                foreign_context,
            )

        entity_decision = self._classify_entity(candidate, context)
        if entity_decision is not None:
            return entity_decision

        return self._semantic_or_report(
            candidate, "candidate_requires_semantic_verification"
        )

    def _classify_glossary(
        self, context: CandidateContext
    ) -> ForeignTextDecision | None:
        matches = match_glossary_policies(context.source_text, self._glossary)
        if not matches:
            return None
        must_translate = tuple(
            match for match in matches if match.policy is GlossaryPolicy.MUST_TRANSLATE
        )
        conflict_reasons = _glossary_conflict_reasons(matches)
        if must_translate:
            reasons = conflict_reasons + tuple(
                f"glossary_must_translate:{match.term}" for match in must_translate
            )
            return ForeignTextDecision(
                "glossary_must_translate",
                "send_to_llm_verifier",
                "high",
                _ordered_unique(reasons),
            )
        if conflict_reasons:
            return ForeignTextDecision(
                "ambiguous", "report_only", "medium", conflict_reasons
            )

        protected = tuple(
            match
            for match in matches
            if match.policy in {GlossaryPolicy.KEEP_ORIGINAL, GlossaryPolicy.EITHER}
        )
        whole = tuple(
            match
            for match in protected
            if _same_meaningful_text(context.source_text, match.term)
        )
        if whole:
            return ForeignTextDecision(
                "glossary_protected",
                "exclude",
                "high",
                tuple(f"glossary_allows_original:{match.term}" for match in whole),
            )
        if protected:
            reasons = tuple(
                f"glossary_protected_term_embedded_in_narrative:{match.term}"
                for match in protected
            )
            return ForeignTextDecision(
                "ambiguous",
                "send_to_llm_verifier",
                "medium",
                _ordered_unique(reasons + ("candidate_requires_semantic_verification",)),
            )
        return None

    @staticmethod
    def _classify_entity(
        candidate: GapCandidate, context: CandidateContext
    ) -> ForeignTextDecision | None:
        matching_hints = tuple(
            hint
            for hint in context.protected_entities
            if _contains_surface(context.source_text, hint.text)
        )
        whole_hints = tuple(
            hint
            for hint in matching_hints
            if _same_meaningful_text(context.source_text, hint.text)
        )
        if whole_hints:
            return ForeignTextDecision(
                "protected_entity",
                "exclude",
                "high",
                tuple(
                    f"explicit_protected_entity:{hint.category}:{hint.text}"
                    for hint in whole_hints
                ),
            )
        if matching_hints:
            reasons = tuple(
                f"protected_entity_embedded_in_narrative:{hint.category}:{hint.text}"
                for hint in matching_hints
            )
            return ForeignTextFilter._semantic_or_report(candidate, *reasons)
        surface = _display_surface(context.source_text)
        if _LATIN_NAME_RE.fullmatch(surface) and surface.split()[0].casefold() not in _NON_NAME_LEADS:
            return _decision(
                "protected_entity",
                "exclude",
                "medium",
                "conservative_full_span_latin_name",
            )
        return None

    @staticmethod
    def _semantic_or_report(
        candidate: GapCandidate, *reasons: str
    ) -> ForeignTextDecision:
        if candidate.repairable:
            return ForeignTextDecision(
                "ambiguous",
                "send_to_llm_verifier",
                "medium",
                _ordered_unique(reasons + ("candidate_requires_semantic_verification",)),
            )
        return ForeignTextDecision(
            "ambiguous",
            "report_only",
            "medium",
            _ordered_unique(reasons + ("candidate_not_repairable_without_two_anchors",)),
        )


def filter_gap_candidates(
    result: AlignmentResult,
    contexts: Mapping[str, CandidateContext],
    glossary: Iterable[GlossaryRule] = (),
) -> CandidateFilterResult:
    """Partition each alignment gap exactly once while preserving every input object."""

    if not isinstance(result, AlignmentResult):
        raise QaModelValidationError("result must be an AlignmentResult")
    if not isinstance(contexts, Mapping):
        raise QaModelValidationError("contexts must be a mapping")
    classifier = ForeignTextFilter(glossary)
    accepted: list[FilteredCandidate] = []
    excluded: list[FilteredCandidate] = []
    report_only: list[FilteredCandidate] = []
    for candidate in result.gaps:
        context = contexts.get(candidate.candidate_id)
        if context is None:
            decision = _decision(
                "invalid_context",
                "report_only",
                "low",
                "candidate_context_missing",
            )
        elif not isinstance(context, CandidateContext):
            decision = _decision(
                "invalid_context",
                "report_only",
                "low",
                "candidate_context_invalid_type",
            )
        else:
            decision = classifier.classify(candidate, context)
        filtered = FilteredCandidate(candidate.candidate_id, candidate, decision)
        if decision.action == "send_to_llm_verifier":
            accepted.append(filtered)
        elif decision.action == "exclude":
            excluded.append(filtered)
        else:
            report_only.append(filtered)
    return CandidateFilterResult(tuple(accepted), tuple(excluded), tuple(report_only))


def _decision(
    category: str, action: str, confidence: str, *reasons: str
) -> ForeignTextDecision:
    return ForeignTextDecision(category, action, confidence, _ordered_unique(reasons))


def _ordered_unique(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip().casefold()


def _meaningful(value: str) -> str:
    return _normalize(value).strip(_WRAPPER_CHARACTERS).strip()


def _display_surface(value: str) -> str:
    normalized = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip()
    return normalized.strip(_WRAPPER_CHARACTERS).strip()


def _same_meaningful_text(left: str, right: str) -> bool:
    return bool(_meaningful(left)) and _meaningful(left) == _meaningful(right)


def _contains_surface(text: str, surface: str) -> bool:
    return bool(
        match_glossary_policies(
            text, (GlossaryRule(surface, GlossaryPolicy.EITHER),)
        )
    )


def _protected_item(text: str) -> tuple[str, bool] | None:
    meaningful = _meaningful(text)
    model_surface = _display_surface(text)
    if _looks_like_full_device_model(model_surface):
        return "model", True
    for category, pattern in _PATTERNS:
        full = pattern.fullmatch(meaningful)
        if full is not None:
            return category, True
        if pattern.search(text) is not None:
            return category, False
    return None


def _looks_like_full_device_model(text: str) -> bool:
    tokens = text.split()
    if not 2 <= len(tokens) <= 5 or not any(any(character.isdigit() for character in token) for token in tokens):
        return False
    for token in tokens:
        compact = token.strip("+._/-")
        if not compact or not all(character.isalnum() or character in "+._/-" for character in token):
            return False
        if any(character.isdigit() for character in compact):
            continue
        if compact.isupper() or compact.istitle():
            continue
        if compact[0].islower() and any(character.isupper() for character in compact[1:]):
            continue
        return False
    return True


def _foreign_context_evidence(context: CandidateContext) -> str | None:
    contexts = set(context.protected_contexts)
    if "sign" in contexts:
        return "explicit_protected_context:sign"
    if "foreign_dialogue" in contexts:
        return "explicit_protected_context:foreign_dialogue"
    if "foreign_quote" in contexts:
        return "explicit_protected_context:foreign_quote"
    language = _language_base(context.candidate_language)
    differs = language not in {
        _language_base(context.source_language),
        _language_base(context.target_language),
        "und",
    }
    if differs and "dialogue" in contexts:
        return "dialogue_language_differs_from_source_and_target"
    if differs and "quote" in contexts:
        return "quote_language_differs_from_source_and_target"
    return None


def _language_base(language: str) -> str:
    return language.replace("_", "-").split("-", 1)[0].casefold()


def _glossary_conflict_reasons(
    matches: tuple[GlossaryPolicyMatch, ...]
) -> tuple[str, ...]:
    by_term: dict[str, list[GlossaryPolicyMatch]] = {}
    for match in matches:
        by_term.setdefault(_normalize(match.term), []).append(match)
    reasons: list[str] = []
    for term_matches in by_term.values():
        policies = tuple(
            sorted({match.policy.value for match in term_matches})
        )
        if len(policies) > 1:
            display = min(
                (match.term for match in term_matches),
                key=lambda term: (_normalize(term), term),
            )
            reasons.append(
                f"glossary_policy_conflict:{display}:{','.join(policies)}"
            )
    return tuple(sorted(reasons, key=lambda reason: (_normalize(reason), reason)))
