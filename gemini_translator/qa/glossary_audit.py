"""Book-wide glossary observation normalization and conflict reporting."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any
import unicodedata

from .glossary_terms import (
    MIN_CANONICAL_TERM_CHARS,
    _QUOTE_TRANSLATION,
    _WORD_RE,
    contains_term_forms,
    glossary_violation_reason,
    match_glossary_policies,
)
from .models import GlossaryObservation, GlossaryPolicy
from .text_normalize import normalize_for_comparison

if TYPE_CHECKING:
    import pandas as pd

# Re-exported for backward compatibility: these three names moved to
# glossary_terms.py so that importing them does not also import pandas --
# GlossaryAuditor below is the only thing in this module that still needs it,
# and it imports pandas lazily (inside each method that touches it) so that
# simply importing this module -- which the five hot glossary-form consumers
# do to reach match_glossary_policies/contains_term_forms/glossary_violation_reason
# -- no longer pulls pandas into sys.modules.
# Callers do `from .glossary_audit import match_glossary_policies` and
# similar, so the names must stay bound at this module's top level.
__all__ = [
    "GlossaryAuditor",
    "MIN_CANONICAL_TERM_CHARS",
    "contains_term_forms",
    "glossary_violation_reason",
    "match_glossary_policies",
]


_DEFAULT_MORPHOLOGY = object()


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
        import pandas as pd

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
        import pandas as pd

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
        import pandas as pd

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
        import pandas as pd

        if not isinstance(frame, pd.DataFrame):
            raise TypeError("frame must be a pandas DataFrame")
        if tuple(frame.columns) != self._OBSERVATION_COLUMNS:
            raise ValueError("frame has an invalid glossary observation schema")

    def _empty_conflicts(self) -> pd.DataFrame:
        import pandas as pd

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
    """Normalize an observed glossary translation for conflict comparison.

    Deliberately more aggressive than the shared `normalize_for_comparison`:
    glossary translations also need curly quotes folded to ASCII, `ё` folded
    to `е`, and wrapping quote characters stripped, none of which the plain
    QA-text comparisons elsewhere in this package want.
    """
    translated = unicodedata.normalize("NFKC", value).translate(_QUOTE_TRANSLATION)
    normalized = normalize_for_comparison(translated).replace("ё", "е")
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
