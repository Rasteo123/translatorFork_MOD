"""Local NLP is evidence: it protects names and suggests, but never edits."""

from __future__ import annotations

import builtins

import pytest

from gemini_translator.qa.capabilities import QaCapabilitySettings
from gemini_translator.qa.foreign_text_filter import ForeignTextFilter
from gemini_translator.qa.models import (
    AlignmentSpan,
    CandidateContext,
    GapCandidate,
    QaModelValidationError,
    SemanticInlineSpan,
    SemanticUnit,
)
from gemini_translator.qa.russian_nlp import (
    RussianNlpService,
    RussianNlpUnavailable,
    SlovnetProvider,
    tokenize,
)
from gemini_translator.qa.russian_nlp.base import MorphologyCandidate, SyntaxCandidate


_TEXT = "Анна вошла в офис Apple в Москве."


class _FakeRuntime:
    """Answers in the same shape the real adapter produces, without the models."""

    def __init__(self, *, spans=None, morphology=(), syntax=(), fail=False) -> None:
        self.spans = spans
        self.morphology = morphology
        self.syntax = syntax
        self.fail = fail

    def ner_spans(self, text):
        if self.fail:
            raise RuntimeError("model exploded")
        if self.spans is not None:
            return self.spans
        return (
            {"start": text.index("Анна"), "stop": text.index("Анна") + 4, "type": "PER"},
            {"start": text.index("Apple"), "stop": text.index("Apple") + 5, "type": "ORG"},
            {"start": text.index("Москве"), "stop": text.index("Москве") + 6, "type": "LOC"},
        )

    def morph_candidates(self, tokens):
        return self.morphology

    def syntax_candidates(self, tokens):
        return self.syntax


def _unit(text: str = _TEXT, *, unit_id="u-1", block_id="b-1") -> SemanticUnit:
    return SemanticUnit(
        unit_id=unit_id,
        document_id="target-doc",
        block_id=block_id,
        ordinal=0,
        text=text,
        normalized_text=text.casefold(),
        source_start=0,
        source_end=len(text),
        kind="paragraph",
        inline_spans=(SemanticInlineSpan("i-1", 0, len(text), 0, len(text)),),
    )


def test_importing_the_package_never_requires_the_optional_libraries(monkeypatch):
    """A user without slovnet installed must still be able to start the app."""
    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name.split(".")[0] in {"slovnet", "navec"}:
            raise ImportError(f"{name} is not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)

    import importlib

    module = importlib.reload(
        importlib.import_module("gemini_translator.qa.russian_nlp")
    )

    assert module.RussianNlpService is not None


def test_a_disabled_capability_never_builds_a_provider():
    """An unchecked box must not load a model or even try to."""

    def forbidden():
        raise AssertionError("the provider must not be built")

    service = RussianNlpService(provider_factory=forbidden)

    result = service.analyze((_unit(),), QaCapabilitySettings())

    assert result.status == "disabled"
    assert result.analysis is None


def test_per_org_loc_entities_keep_their_exact_ranges():
    """A protected name is only protected if its range is exactly right."""
    report = SlovnetProvider(_FakeRuntime()).analyze((_unit(),))

    assert [
        (item.text, item.entity_type) for item in report.protected_entities
    ] == [("Анна", "PER"), ("Apple", "ORG"), ("Москве", "LOC")]
    assert all(
        _TEXT[item.start : item.end] == item.text
        for item in report.protected_entities
    )


def test_entities_make_the_foreign_text_filter_stop_short_of_a_repair():
    """A recognised name inside prose must never be auto-translated away."""
    report = SlovnetProvider(_FakeRuntime()).analyze((_unit(),))
    candidate = GapCandidate(
        candidate_id="gap-" + "a" * 20,
        side="source",
        source_unit_ids=("s-1",),
        target_unit_ids=(),
        left_anchor=AlignmentSpan(("s-0",), ("t-0",), 0.95, "1:1"),
        right_anchor=AlignmentSpan(("s-2",), ("t-2",), 0.95, "1:1"),
        repairable=True,
        signals=("missing_in_target",),
    )
    context = CandidateContext(
        candidate_id=candidate.candidate_id,
        source_text="Apple",
        target_text="",
        source_before="a",
        source_after="b",
        target_before="в",
        target_after="г",
        source_language="en",
        target_language="ru",
        candidate_language="en",
        protected_entities=report.protection_hints(),
    )

    decision = ForeignTextFilter().classify(candidate, context)

    assert decision.action == "exclude"
    assert any("organization" in reason for reason in decision.reasons)


def test_morphology_and_syntax_candidates_can_never_authorize_an_edit():
    """Every local candidate is a suggestion; the model decides, not the parser."""
    runtime = _FakeRuntime(
        morphology=({"token_id": 1, "category": "case_agreement", "confidence": "high"},),
        syntax=({"token_ids": (0, 1), "category": "unusual_syntax", "confidence": "medium"},),
    )

    report = SlovnetProvider(runtime).analyze((_unit(),))

    assert report.morphology_candidates[0].auto_fix_allowed is False
    assert report.syntax_candidates[0].auto_fix_allowed is False
    with pytest.raises(QaModelValidationError):
        MorphologyCandidate(
            unit_id="u-1",
            block_id="b-1",
            start=0,
            end=4,
            category="case",
            auto_fix_allowed=True,  # type: ignore[arg-type]
        )
    with pytest.raises(QaModelValidationError):
        SyntaxCandidate(
            unit_id="u-1",
            block_id="b-1",
            token_ids=(0,),
            category="syntax",
            auto_fix_allowed=True,  # type: ignore[arg-type]
        )


def test_an_ambiguous_parse_stays_ambiguous():
    """An unusual literary line must not be reported as a confident defect."""
    runtime = _FakeRuntime(
        syntax=({"token_ids": (0,), "category": "unusual_syntax", "confidence": "нет"},),
    )

    report = SlovnetProvider(runtime).analyze((_unit(),))

    assert report.syntax_candidates[0].confidence == "ambiguous"


@pytest.mark.parametrize(
    "spans",
    [
        ({"start": -1, "stop": 4, "type": "PER"},),
        ({"start": 0, "stop": 0, "type": "PER"},),
        ({"start": 0, "stop": 9999, "type": "PER"},),
        ({"start": 0, "stop": 4, "type": "MISC"},),
        ("not a span",),
    ],
)
def test_unusable_model_output_is_dropped_not_guessed(spans):
    """A model that answers oddly must produce no protection at all."""
    report = SlovnetProvider(_FakeRuntime(spans=spans)).analyze((_unit(),))

    assert report.protected_entities == ()


def test_a_failing_model_is_an_outage_not_a_crash():
    """A broken analyzer must leave the chapter check running."""
    service = RussianNlpService(provider_factory=lambda: SlovnetProvider(_FakeRuntime(fail=True)))

    result = service.analyze((_unit(),), QaCapabilitySettings(slovnet_enabled=True))

    assert result.status == "completed"
    assert result.analysis.protected_entities == ()


def test_a_provider_that_cannot_be_built_is_reported_once():
    """A missing install must be explained, and not retried on every chapter."""
    attempts = []

    def factory():
        attempts.append(1)
        raise RussianNlpUnavailable("slovnet_packages_missing")

    service = RussianNlpService(provider_factory=factory)
    capabilities = QaCapabilitySettings(slovnet_enabled=True)

    first = service.analyze((_unit(),), capabilities)
    second = service.analyze((_unit(),), capabilities)

    assert first.status == "unavailable"
    assert first.warnings == ("slovnet_packages_missing",)
    assert second.warnings == ("slovnet_packages_missing",)
    assert len(attempts) == 1


def test_hints_reach_the_language_review_without_the_library_types():
    """The diagnosis prompt must see plain text, never a Natasha object."""
    runtime = _FakeRuntime(
        syntax=({"token_ids": (0, 1), "category": "unusual_syntax"},),
    )

    analysis = SlovnetProvider(runtime).analyze((_unit(),)).as_analysis()

    assert [entity.text for entity in analysis.entities] == ["Анна", "Apple", "Москве"]
    assert analysis.entities[0].block_id == "b-1"
    assert analysis.syntax_candidates[0].reason == "unusual_syntax"
    assert analysis.syntax_candidates[0].original_text == "Анна вошла"


def test_tokenizer_offsets_match_the_unit_text():
    """Token offsets are what every candidate range is built from."""
    tokens = tokenize("Анна вошла в офис.")

    assert [token for token, _start, _end in tokens] == ["Анна", "вошла", "в", "офис"]
    assert all(
        "Анна вошла в офис."[start:end] == token for token, start, end in tokens
    )


def test_cpu_limits_are_bounded_to_a_safe_maximum():
    """A hand-edited config must not turn a background analyzer into a load spike."""
    provider = SlovnetProvider(_FakeRuntime(), cpu_threads=9999, batch_size=9999)

    assert provider.cpu_threads == 16
    assert provider.batch_size == 128
