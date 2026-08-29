from dataclasses import FrozenInstanceError
import json

import pandas as pd
import pytest

from gemini_translator.qa.glossary_audit import GlossaryAuditor
from gemini_translator.qa.journal import QaJournal, QaJournalCorruptedError
from gemini_translator.qa.models import (
    GlossaryObservation,
    GlossaryPolicy,
    QaModelValidationError,
)


class _Parse:
    def __init__(self, normal_form: str) -> None:
        self.normal_form = normal_form


class FakeMorphology:
    """Small deterministic morphology double for the auditor boundary."""

    _FORMS = {
        "зал": ("зал",),
        "зала": ("зал",),
        "залом": ("зал",),
        "духов": ("дух",),
        "храм": ("храм",),
        "боевых": ("боевой",),
        "душ": ("душа",),
    }

    def parse(self, token: str) -> list[_Parse]:
        return [_Parse(form) for form in self._FORMS.get(token, ())]


def observation(
    original_term: str,
    observed_translation: str,
    *,
    canonical_translation: str | None = None,
    chapter: str = "1",
    occurrences: int = 1,
    policy: GlossaryPolicy = GlossaryPolicy.MUST_TRANSLATE,
) -> GlossaryObservation:
    return GlossaryObservation(
        original_term=original_term,
        observed_translation=observed_translation,
        canonical_translation=canonical_translation,
        morphology_signature=(),
        morphology_confidence="ambiguous",
        chapter_id=chapter,
        occurrences=occurrences,
        policy=policy,
    )


def repeated_observations(
    original_term: str, translation: str, *, chapters: int
) -> list[GlossaryObservation]:
    return [
        observation(original_term, translation, chapter=str(chapter))
        for chapter in range(chapters)
    ]


def test_statistical_minority_is_reported_but_requires_llm_confirmation():
    """Treating frequency as a text edit would hide a statistics-only judgment."""
    observations = repeated_observations("武魂殿", "Зал Духов", chapters=95)
    observations += repeated_observations("武魂殿", "Храм Боевых Душ", chapters=3)

    conflicts = GlossaryAuditor(morphology=FakeMorphology()).conflicts_from_observations(
        observations
    )

    conflict = conflicts.iloc[0]
    assert conflict.original_term == "武魂殿"
    assert conflict.dominant_translation == "Зал Духов"
    assert conflict.minority_translation == "Храм Боевых Душ"
    assert bool(conflict.requires_llm_confirmation) is True
    assert bool(conflict.high_confidence) is False


def test_explicit_canonical_translation_overrides_statistical_majority():
    """Selecting the dominant family instead of canonical text would flag the wrong form."""
    observations = repeated_observations("武魂殿", "Храм Боевых Душ", chapters=5)
    observations.append(
        observation(
            "武魂殿",
            "Зал Духов",
            canonical_translation="Зал Духов",
            chapter="6",
        )
    )
    observations = [
        GlossaryObservation(
            original_term=item.original_term,
            observed_translation=item.observed_translation,
            canonical_translation="Зал Духов",
            morphology_signature=item.morphology_signature,
            morphology_confidence=item.morphology_confidence,
            chapter_id=item.chapter_id,
            occurrences=item.occurrences,
            policy=item.policy,
        )
        for item in observations
    ]

    conflicts = GlossaryAuditor(morphology=FakeMorphology()).conflicts_from_observations(
        observations
    )

    assert list(conflicts["minority_translation"]) == ["Храм Боевых Душ"]
    assert list(conflicts["requires_llm_confirmation"]) == [False]
    assert list(conflicts["high_confidence"]) == [False]


def test_inflected_russian_forms_share_a_high_confidence_family():
    """Literal equality would make valid Russian declensions look inconsistent."""
    auditor = GlossaryAuditor(morphology=FakeMorphology())

    frame = auditor.observations_frame(
        [
            observation("武魂殿", "Зал Духов", chapter="1"),
            observation("武魂殿", "Зала Духов", chapter="2"),
            observation("武魂殿", "Залом Духов", chapter="3"),
        ]
    )

    assert frame["morphology_family"].nunique() == 1
    assert set(frame["morphology_confidence"]) == {"high"}
    assert auditor.conflicts(frame).empty


def test_lexically_different_translation_is_not_collapsed_as_inflection():
    """Merging unrelated lemma paths would suppress a genuine glossary conflict."""
    auditor = GlossaryAuditor(morphology=FakeMorphology())
    frame = auditor.observations_frame(
        [
            observation("武魂殿", "Зал Духов", chapter="1"),
            observation("武魂殿", "Храм Боевых Душ", chapter="2"),
        ]
    )

    assert frame["morphology_family"].nunique() == 2
    assert auditor.conflicts(frame).iloc[0].minority_translation == "Храм Боевых Душ"


def test_different_token_counts_are_ambiguous_not_high_confidence():
    """Comparing two-token and three-token phrases as lemma families is unsafe."""
    frame = GlossaryAuditor(morphology=FakeMorphology()).observations_frame(
        [
            observation("武魂殿", "Зал Духов", chapter="1"),
            observation("武魂殿", "Храм Боевых Душ", chapter="2"),
        ]
    )

    assert set(frame["morphology_confidence"]) == {"ambiguous"}


def test_normalization_preserves_surface_but_unifies_case_quotes_spaces_and_yo():
    """Overwriting the surface form would make the QA report impossible to audit."""
    frame = GlossaryAuditor(morphology=FakeMorphology()).observations_frame(
        [
            observation("武魂殿", "  «Зал Ёлки»  "),
            observation("武魂殿", "„зал елки“"),
        ]
    )

    assert list(frame["observed_translation"]) == ["  «Зал Ёлки»  ", "„зал елки“"]
    assert frame["normalized_translation"].nunique() == 1


def test_unavailable_or_failed_morphology_is_ambiguous_and_never_high_confidence():
    """Promoting unanalyzed strings to high confidence risks unsafe automatic edits."""
    frame = GlossaryAuditor(morphology=None).observations_frame(
        [
            observation("武魂殿", "Зал Духов", chapter="1"),
            observation("武魂殿", "Зала Духов", chapter="2"),
        ]
    )

    conflicts = GlossaryAuditor(morphology=None).conflicts(frame)

    assert set(frame["morphology_confidence"]) == {"ambiguous"}
    assert conflicts.empty or not conflicts["high_confidence"].any()


@pytest.mark.parametrize(
    ("policy", "observed", "canonical", "has_conflict"),
    [
        (GlossaryPolicy.KEEP_ORIGINAL, "Soul Hall", "Зал Духов", False),
        (GlossaryPolicy.EITHER, "Soul Hall", "Зал Духов", False),
        (GlossaryPolicy.MUST_TRANSLATE, "Soul Hall", "Зал Духов", True),
    ],
)
def test_glossary_policy_defines_when_original_surface_is_allowed(
    policy, observed, canonical, has_conflict
):
    """Ignoring the policy would report intentionally preserved terms as defects."""
    conflicts = GlossaryAuditor(morphology=FakeMorphology()).conflicts_from_observations(
        [observation("Soul Hall", observed, canonical_translation=canonical, policy=policy)]
    )

    assert (not conflicts.empty) is has_conflict


def test_observation_contract_is_frozen_strict_and_json_round_trippable():
    """Loose observation values would let a malformed v1 collection silently persist."""
    item = observation("武魂殿", "Зал Духов", occurrences=2)

    assert item.to_dict()["policy"] == "must_translate"
    assert GlossaryObservation.from_dict(item.to_dict()) == item
    with pytest.raises(FrozenInstanceError):
        item.occurrences = 3
    with pytest.raises(QaModelValidationError, match="positive"):
        observation("武魂殿", "Зал Духов", occurrences=0)
    with pytest.raises(QaModelValidationError):
        GlossaryObservation.from_dict({"original_term": "武魂殿"})


def test_journal_round_trips_typed_observations_and_rejects_invalid_v1_entries(tmp_path):
    """Accepting generic observation dicts would lose the typed journal contract."""
    journal = QaJournal.empty(book_id="book-1")
    journal.append_glossary_observation(observation("武魂殿", "Зал Духов"))
    path = tmp_path / "translation_qa.json"
    journal.save(path)

    restored = QaJournal.load(path)

    assert restored.glossary_observations == [observation("武魂殿", "Зал Духов")]
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["glossary_observations"] = [{"original_term": "武魂殿"}]
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(QaJournalCorruptedError):
        QaJournal.load(path)


def test_observations_frame_and_conflicts_have_stable_empty_schemas():
    """Dropping columns for empty books breaks downstream report construction."""
    auditor = GlossaryAuditor(morphology=None)

    frame = auditor.observations_frame([])
    conflicts = auditor.conflicts(frame)

    assert list(frame.columns) == [
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
    ]
    assert list(conflicts.columns) == [
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
    ]
    assert isinstance(frame, pd.DataFrame)
