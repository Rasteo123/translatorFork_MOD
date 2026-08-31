"""Book-wide glossary observation normalization and conflict reporting."""

from __future__ import annotations

from collections.abc import Iterable
import re
from typing import Any
import unicodedata

import pandas as pd

from .models import (
    GlossaryObservation,
    GlossaryPolicy,
    GlossaryPolicyMatch,
    GlossaryRule,
    RelevantGlossaryTerm,
)


_DEFAULT_MORPHOLOGY = object()
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
    normalized = unicodedata.normalize("NFKC", value).translate(_QUOTE_TRANSLATION)
    return re.sub(r"\s+", " ", normalized).strip().casefold()


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


class GlossaryAuditor:
    """Classify observed glossary translations without turning frequency into edits."""

    _OBSERVATION_COLUMNS = (
        "original_term",
        "observed_translation",
        "normalized_translation",
        "morphology_signature",
        "morphology_family",
        "morphology_confidence",
        "canonical_translation",
        "chapter_id",
        "occurrences",
        "policy",
    )
    _CONFLICT_COLUMNS = (
        "original_term",
        "dominant_translation",
        "minority_translation",
        "canonical_translation",
        "dominant_occurrences",
        "minority_occurrences",
        "surface_forms",
        "policy",
        "high_confidence",
        "requires_llm_confirmation",
    )

    def __init__(self, morphology: Any = _DEFAULT_MORPHOLOGY) -> None:
        self._morphology = morphology

    def observations_frame(
        self, observations: Iterable[GlossaryObservation]
    ) -> pd.DataFrame:
        rows: list[dict[str, Any]] = []
        for observation in observations:
            if not isinstance(observation, GlossaryObservation):
                raise TypeError("observations must be GlossaryObservation instances")
            normalized = _normalize(observation.observed_translation)
            signature, confidence = self._signature(normalized)
            rows.append(
                {
                    "original_term": observation.original_term,
                    "observed_translation": observation.observed_translation,
                    "normalized_translation": normalized,
                    "morphology_signature": signature,
                    "morphology_family": "",
                    "morphology_confidence": confidence,
                    "canonical_translation": observation.canonical_translation,
                    "chapter_id": observation.chapter_id,
                    "occurrences": observation.occurrences,
                    "policy": observation.policy.value,
                }
            )

        if not rows:
            return pd.DataFrame(
                {
                    "original_term": pd.Series(dtype="object"),
                    "observed_translation": pd.Series(dtype="object"),
                    "normalized_translation": pd.Series(dtype="object"),
                    "morphology_signature": pd.Series(dtype="object"),
                    "morphology_family": pd.Series(dtype="object"),
                    "morphology_confidence": pd.Series(dtype="object"),
                    "canonical_translation": pd.Series(dtype="object"),
                    "chapter_id": pd.Series(dtype="object"),
                    "occurrences": pd.Series(dtype="int64"),
                    "policy": pd.Series(dtype="object"),
                },
                columns=self._OBSERVATION_COLUMNS,
            )

        self._assign_morphology_families(rows)
        return pd.DataFrame(rows, columns=self._OBSERVATION_COLUMNS)

    def conflicts_from_observations(
        self, observations: Iterable[GlossaryObservation]
    ) -> pd.DataFrame:
        return self.conflicts(self.observations_frame(observations))

    def conflicts(self, frame: pd.DataFrame) -> pd.DataFrame:
        self._validate_frame(frame)
        if frame.empty:
            return self._empty_conflicts()

        family_counts = (
            frame.groupby(["original_term", "morphology_family"], observed=True, sort=True)[
                "occurrences"
            ]
            .sum()
            .rename("occurrences")
            .reset_index()
        )
        surfaces = (
            frame.groupby(["original_term", "morphology_family"], observed=True, sort=True)[
                "observed_translation"
            ]
            .agg(_ordered_unique)
            .rename("surface_forms")
            .reset_index()
        )
        family_summary = family_counts.merge(
            surfaces, on=["original_term", "morphology_family"], how="inner", validate="one_to_one"
        )
        family_confidence = (
            frame.assign(_family_high=frame["morphology_confidence"].eq("high"))
            .groupby(["original_term", "morphology_family"], observed=True, sort=True)[
                "_family_high"
            ]
            .all()
            .reset_index()
        )
        family_summary = family_summary.merge(
            family_confidence,
            on=["original_term", "morphology_family"],
            how="inner",
            validate="one_to_one",
        )
        results: list[dict[str, Any]] = []
        for original_term, term_frame in frame.groupby("original_term", observed=True, sort=True):
            summary = family_summary[family_summary["original_term"] == original_term]
            canonical = _first_non_null(term_frame["canonical_translation"])
            policy = _policy_for_term(term_frame["policy"])
            if canonical is not None:
                results.extend(
                    self._canonical_conflicts(term_frame, summary, canonical, policy)
                )
            else:
                results.extend(self._statistical_conflicts(term_frame, summary, policy))
        if not results:
            return self._empty_conflicts()
        return pd.DataFrame(results, columns=self._CONFLICT_COLUMNS).sort_values(
            ["original_term", "minority_translation", "canonical_translation"],
            kind="stable",
            na_position="first",
        ).reset_index(drop=True)

    def _canonical_conflicts(
        self,
        term_frame: pd.DataFrame,
        summary: pd.DataFrame,
        canonical: str,
        policy: GlossaryPolicy,
    ) -> list[dict[str, Any]]:
        canonical_normalized = _normalize(canonical)
        canonical_signature, canonical_confidence = self._signature(canonical_normalized)
        original_normalized = _normalize(str(term_frame["original_term"].iloc[0]))
        allowed = term_frame.apply(
            lambda row: self._allowed_by_policy(
                row["normalized_translation"],
                original_normalized,
                canonical_normalized,
                row["morphology_signature"],
                row["morphology_confidence"],
                canonical_signature,
                canonical_confidence,
                GlossaryPolicy(row["policy"]),
            ),
            axis=1,
        )
        violating = term_frame.loc[~allowed]
        if violating.empty:
            return []
        candidate_summary = (
            violating.groupby("morphology_family", observed=True, sort=True)["occurrences"]
            .sum()
            .rename("minority_occurrences")
            .reset_index()
            .merge(
                violating.groupby("morphology_family", observed=True, sort=True)[
                    "observed_translation"
                ]
                .agg(_ordered_unique)
                .rename("surface_forms")
                .reset_index(),
                on="morphology_family",
                validate="one_to_one",
            )
        )
        canonical_occurrences = int(
            term_frame.loc[allowed, "occurrences"].sum()
        )
        rows: list[dict[str, Any]] = []
        for candidate in candidate_summary.sort_values(
            ["minority_occurrences", "morphology_family"], ascending=[False, True], kind="stable"
        ).to_dict("records"):
            family_rows = violating[
                violating["morphology_family"] == candidate["morphology_family"]
            ]
            high_confidence = bool(
                (family_rows["morphology_confidence"] == "high").all()
                and canonical_confidence == "high"
                and _confirmed_different_morphology(
                    family_rows["morphology_signature"].iloc[0], canonical_signature
                )
            )
            rows.append(
                {
                    "original_term": term_frame["original_term"].iloc[0],
                    "dominant_translation": canonical,
                    "minority_translation": candidate["surface_forms"][0],
                    "canonical_translation": canonical,
                    "dominant_occurrences": canonical_occurrences,
                    "minority_occurrences": int(candidate["minority_occurrences"]),
                    "surface_forms": candidate["surface_forms"],
                    "policy": policy.value,
                    "high_confidence": high_confidence,
                    "requires_llm_confirmation": True,
                }
            )
        return rows

    def _statistical_conflicts(
        self, term_frame: pd.DataFrame, summary: pd.DataFrame, policy: GlossaryPolicy
    ) -> list[dict[str, Any]]:
        if len(summary) < 2:
            return []
        ranked = summary.sort_values(
            ["occurrences", "_family_high", "morphology_family"],
            ascending=[False, False, True],
            kind="stable",
        ).reset_index(drop=True)
        dominant = ranked.iloc[0]
        rows: list[dict[str, Any]] = []
        for minority in ranked.iloc[1:].to_dict("records"):
            rows.append(
                {
                    "original_term": term_frame["original_term"].iloc[0],
                    "dominant_translation": dominant.surface_forms[0],
                    "minority_translation": minority["surface_forms"][0],
                    "canonical_translation": None,
                    "dominant_occurrences": int(dominant.occurrences),
                    "minority_occurrences": int(minority["occurrences"]),
                    "surface_forms": minority["surface_forms"],
                    "policy": policy.value,
                    "high_confidence": False,
                    "requires_llm_confirmation": True,
                }
            )
        return rows

    def _allowed_by_policy(
        self,
        normalized: str,
        original: str,
        canonical: str,
        signature: tuple[str, ...],
        confidence: str,
        canonical_signature: tuple[str, ...],
        canonical_confidence: str,
        policy: GlossaryPolicy,
    ) -> bool:
        if normalized == original and policy in {
            GlossaryPolicy.KEEP_ORIGINAL,
            GlossaryPolicy.EITHER,
        }:
            return True
        if normalized == canonical:
            return True
        return (
            confidence == "high"
            and canonical_confidence == "high"
            and _same_morphology_family(signature, canonical_signature)
        )

    def _assign_morphology_families(self, rows: list[dict[str, Any]]) -> None:
        by_original: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            by_original.setdefault(row["original_term"], []).append(row)
        for original_term, term_rows in by_original.items():
            high_rows = sorted(
                (row for row in term_rows if row["morphology_confidence"] == "high"),
                key=lambda row: row["normalized_translation"],
            )
            canonical = _first_non_null(
                pd.Series(row["canonical_translation"] for row in term_rows)
            )
            canonical_signature: tuple[str, ...] = ()
            canonical_confidence = "ambiguous"
            if canonical is not None:
                canonical_signature, canonical_confidence = self._signature(
                    _normalize(canonical)
                )
            token_counts = {len(row["morphology_signature"]) for row in high_rows}
            if canonical_confidence == "high":
                reliable_count = len(canonical_signature)
                for row in high_rows:
                    if len(row["morphology_signature"]) != reliable_count:
                        row["morphology_confidence"] = "ambiguous"
                        row["morphology_signature"] = ()
                high_rows = [
                    row for row in high_rows if row["morphology_confidence"] == "high"
                ]
            elif len(token_counts) > 1:
                for row in high_rows:
                    row["morphology_confidence"] = "ambiguous"
                    row["morphology_signature"] = ()
                high_rows = []
            families: list[dict[str, Any]] = []
            for row in high_rows:
                matching = next(
                    (
                        family
                        for family in families
                        if _same_morphology_family(
                            row["morphology_signature"], family["signature"]
                        )
                    ),
                    None,
                )
                if matching is None:
                    matching = {"signature": row["morphology_signature"], "rows": []}
                    families.append(matching)
                else:
                    matching["signature"] = _intersect_signatures(
                        matching["signature"], row["morphology_signature"]
                    )
                matching["rows"].append(row)
            families.sort(key=lambda family: min(row["normalized_translation"] for row in family["rows"]))
            for index, family in enumerate(families):
                family_id = f"{original_term}|high|{index}"
                for row in family["rows"]:
                    row["morphology_family"] = family_id
            for row in term_rows:
                if row["morphology_confidence"] == "ambiguous":
                    row["morphology_family"] = (
                        f"{original_term}|ambiguous|{row['normalized_translation']}"
                    )

    def _signature(self, normalized: str) -> tuple[tuple[str, ...], str]:
        analyzer = self._get_morphology()
        tokens = _WORD_RE.findall(normalized)
        if analyzer is None or not tokens:
            return (), "ambiguous"
        signatures: list[str] = []
        try:
            for token in tokens:
                forms = {
                    str(parse.normal_form).casefold().replace("ё", "е")
                    for parse in analyzer.parse(token)
                    if isinstance(getattr(parse, "normal_form", None), str)
                    and parse.normal_form
                }
                if not forms:
                    return (), "ambiguous"
                signatures.append("|".join(sorted(forms)))
        except Exception:
            return (), "ambiguous"
        return tuple(signatures), "high"

    def _get_morphology(self) -> Any:
        if self._morphology is _DEFAULT_MORPHOLOGY:
            from gemini_translator.utils.morphology import get_morph_analyzer

            self._morphology = get_morph_analyzer()
        return self._morphology

    def _validate_frame(self, frame: pd.DataFrame) -> None:
        if not isinstance(frame, pd.DataFrame):
            raise TypeError("frame must be a pandas DataFrame")
        if tuple(frame.columns) != self._OBSERVATION_COLUMNS:
            raise ValueError("frame has an invalid glossary observation schema")

    def _empty_conflicts(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "original_term": pd.Series(dtype="object"),
                "dominant_translation": pd.Series(dtype="object"),
                "minority_translation": pd.Series(dtype="object"),
                "canonical_translation": pd.Series(dtype="object"),
                "dominant_occurrences": pd.Series(dtype="int64"),
                "minority_occurrences": pd.Series(dtype="int64"),
                "surface_forms": pd.Series(dtype="object"),
                "policy": pd.Series(dtype="object"),
                "high_confidence": pd.Series(dtype="bool"),
                "requires_llm_confirmation": pd.Series(dtype="bool"),
            },
            columns=self._CONFLICT_COLUMNS,
        )


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).translate(_QUOTE_TRANSLATION)
    normalized = re.sub(r"\s+", " ", normalized).strip().casefold().replace("ё", "е")
    return normalized.strip("'\"")


def _ordered_unique(values: pd.Series) -> tuple[str, ...]:
    return tuple(sorted(set(values.tolist()), key=lambda value: (_normalize(value), value)))


def _first_non_null(values: pd.Series) -> str | None:
    for value in values.tolist():
        if value is not None:
            return value
    return None


def _policy_for_term(values: pd.Series) -> GlossaryPolicy:
    policies = {GlossaryPolicy(value) for value in values.tolist()}
    if GlossaryPolicy.MUST_TRANSLATE in policies:
        return GlossaryPolicy.MUST_TRANSLATE
    if GlossaryPolicy.EITHER in policies:
        return GlossaryPolicy.EITHER
    return GlossaryPolicy.KEEP_ORIGINAL


def _same_morphology_family(
    left: tuple[str, ...], right: tuple[str, ...]
) -> bool:
    return bool(left) and len(left) == len(right) and all(
        bool(set(left_forms.split("|")) & set(right_forms.split("|")))
        for left_forms, right_forms in zip(left, right, strict=True)
    )


def _confirmed_different_morphology(
    left: tuple[str, ...], right: tuple[str, ...]
) -> bool:
    return bool(left) and len(left) == len(right) and any(
        not (set(left_forms.split("|")) & set(right_forms.split("|")))
        for left_forms, right_forms in zip(left, right, strict=True)
    )


def _intersect_signatures(
    left: tuple[str, ...], right: tuple[str, ...]
) -> tuple[str, ...]:
    return tuple(
        "|".join(sorted(set(left_forms.split("|")) & set(right_forms.split("|"))))
        for left_forms, right_forms in zip(left, right, strict=True)
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
        canonical = term.canonical_translation.strip()
        if canonical and not contains_term_forms(fragment, canonical):
            return "canonical_term_missing"
    return ""
