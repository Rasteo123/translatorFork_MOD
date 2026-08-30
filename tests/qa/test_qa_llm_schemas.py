"""Fail-closed contracts for structured QA responses."""

from dataclasses import FrozenInstanceError
import math

import pytest

from gemini_translator.qa.llm import (
    LanguageIssue,
    OmissionVerdict,
    QaResponseSchemaError,
    RepairProposal,
    parse_single_json_object,
)


def _omission_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "decision": "missing_content",
        "confidence": 0.99,
        "source_unit_ids": ["source-1"],
        "missing_facts": ["Герой не согласился."],
        "explanation": "В переводе потеряно отрицание.",
    }
    payload.update(overrides)
    return payload


def _language_issue_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "issue_id": "issue-1",
        "category": "grammar",
        "block_id": "block-1",
        "original_text": "ихний дом",
        "replacement_text": "их дом",
        "objective": True,
        "confidence": 0.95,
        "explanation": "Неверная форма притяжательного местоимения.",
    }
    payload.update(overrides)
    return payload


def test_json_fence_is_accepted_but_trailing_prose_is_rejected():
    """Accepting prose after JSON could authorize a partial model response."""
    assert parse_single_json_object('```json\n{"decision":"no_gap"}\n```') == {
        "decision": "no_gap"
    }

    with pytest.raises(QaResponseSchemaError):
        parse_single_json_object('{"decision":"no_gap"}\nЯ всё проверил.')


@pytest.mark.parametrize(
    "payload",
    [
        '{"decision":"no_gap"}{"decision":"covered"}',
        '[{"decision":"no_gap"}]',
        '```json\n{"decision":"no_gap"}\n```\n```json\n{}\n```',
        'text\n```json\n{"decision":"no_gap"}\n```',
        '```python\n{"decision":"no_gap"}\n```',
        '```json {"decision":"no_gap"}```',
        '{"confidence": NaN}',
        '{"decision":"covered","decision":"missing_content"}',
    ],
)
def test_json_parser_rejects_multiple_nonobject_or_nonstandard_payloads(payload):
    """Relaxing the parser boundary could select an unintended object."""
    with pytest.raises(QaResponseSchemaError):
        parse_single_json_object(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"decision": "missing_content", "confidence": 1.4},
        {
            "decision": "missing_content",
            "confidence": 0.99,
            "source_unit_ids": ["source-1"],
            "missing_facts": [],
            "explanation": "Нет фактов.",
        },
        {
            "decision": "unknown",
            "confidence": 0.99,
            "source_unit_ids": ["source-1"],
            "missing_facts": ["fact"],
            "explanation": "Неизвестное решение.",
        },
    ],
)
def test_incomplete_or_invalid_verdict_never_authorizes_repair(payload):
    """Weak verdict validation could authorize repair without complete evidence."""
    with pytest.raises(QaResponseSchemaError):
        OmissionVerdict.from_dict(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("confidence", math.nan),
        ("confidence", math.inf),
        ("confidence", True),
        ("confidence", -0.01),
        ("decision", []),
        ("source_unit_ids", []),
        ("source_unit_ids", [""]),
        ("source_unit_ids", ["source-1", "source-1"]),
        ("missing_facts", [""]),
        ("explanation", " "),
    ],
)
def test_omission_verdict_rejects_invalid_ranges_ids_and_text(field, value):
    """Invalid evidence must fail before any repair eligibility decision."""
    with pytest.raises(QaResponseSchemaError):
        OmissionVerdict.from_dict(_omission_payload(**{field: value}))


def test_non_missing_verdict_may_have_no_missing_facts():
    """A covered verdict remains representable without inventing missing facts."""
    verdict = OmissionVerdict.from_dict(
        _omission_payload(decision="covered", missing_facts=[])
    )

    assert verdict.missing_facts == ()


@pytest.mark.parametrize(
    "payload",
    [
        _omission_payload(extra="ignored"),
        _omission_payload(metadata=[]),
        _omission_payload(metadata={"": "value"}),
    ],
)
def test_unknown_fields_and_malformed_metadata_are_rejected(payload):
    """Silently ignored fields could hide schema drift in authorizing responses."""
    with pytest.raises(QaResponseSchemaError):
        OmissionVerdict.from_dict(payload)


def test_schema_collections_and_metadata_are_immutable_copies():
    """Mutating validated evidence after authorization must not change the verdict."""
    source_ids = ["source-1"]
    nested = ["first"]
    payload = _omission_payload(metadata={"trace": nested})
    payload["source_unit_ids"] = source_ids

    verdict = OmissionVerdict.from_dict(payload)
    source_ids.append("source-2")
    nested.append("second")

    assert verdict.source_unit_ids == ("source-1",)
    assert verdict.metadata["trace"] == ("first",)
    with pytest.raises(TypeError):
        verdict.metadata["new"] = "value"
    with pytest.raises(FrozenInstanceError):
        verdict.confidence = 0.1


def test_repair_proposal_requires_expected_candidate_identity():
    """A proposal for another candidate must never be applied to this request."""
    payload = {
        "candidate_id": "candidate-1",
        "translated_fragment": "Он не согласился.",
        "glossary_terms_used": ["term-1"],
    }

    proposal = RepairProposal.from_dict(payload, expected_candidate_id="candidate-1")
    assert proposal.candidate_id == "candidate-1"
    assert proposal.glossary_terms_used == ("term-1",)

    with pytest.raises(QaResponseSchemaError):
        RepairProposal.from_dict(payload, expected_candidate_id="candidate-2")


def test_schema_validation_preserves_exact_nonempty_text():
    """Trimming a validated fragment would mutate the proposed structural content."""
    proposal = RepairProposal.from_dict(
        {
            "candidate_id": "candidate-1",
            "translated_fragment": "  Он не согласился.  ",
            "glossary_terms_used": [],
        },
        expected_candidate_id="candidate-1",
    )

    assert proposal.translated_fragment == "  Он не согласился.  "


@pytest.mark.parametrize(
    "overrides",
    [
        {"candidate_id": ""},
        {"translated_fragment": " "},
        {"glossary_terms_used": [""]},
        {"glossary_terms_used": ["term-1", "term-1"]},
        {"unexpected": True},
    ],
)
def test_repair_proposal_rejects_empty_or_unknown_data(overrides):
    """Malformed proposals must fail before reaching structural repair."""
    payload = {
        "candidate_id": "candidate-1",
        "translated_fragment": "Он не согласился.",
        "glossary_terms_used": ["term-1"],
    }
    payload.update(overrides)

    with pytest.raises(QaResponseSchemaError):
        RepairProposal.from_dict(payload, expected_candidate_id="candidate-1")


@pytest.mark.parametrize(
    "overrides",
    [
        {"issue_id": ""},
        {"category": "rewrite_everything"},
        {"category": []},
        {"block_id": " "},
        {"original_text": ""},
        {"replacement_text": ""},
        {"replacement_text": 42},
        {"objective": 1},
        {"confidence": -0.1},
        {"confidence": math.inf},
        {"explanation": ""},
        {"unknown": "field"},
    ],
)
def test_language_issue_rejects_invalid_authorizing_fields(overrides):
    """Invalid language issues must remain unable to authorize replacement."""
    with pytest.raises(QaResponseSchemaError):
        LanguageIssue.from_dict(_language_issue_payload(**overrides))


def test_language_issue_accepts_explicit_null_replacement():
    """Report-only issues can intentionally omit a replacement."""
    issue = LanguageIssue.from_dict(
        _language_issue_payload(
            category="style_suggestion",
            replacement_text=None,
            objective=False,
        )
    )

    assert issue.replacement_text is None
