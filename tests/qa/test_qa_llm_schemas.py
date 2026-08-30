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


def test_unfenced_json_may_contain_markdown_backticks_inside_string_value():
    """Treating embedded backticks as a fence would reject valid model text."""
    assert parse_single_json_object('{"explanation":"Use ``` as data"}') == {
        "explanation": "Use ``` as data"
    }


def test_invalid_json_error_retains_no_raw_response_or_decoder_exception():
    """Decoder errors and parser frames must not retain a sensitive raw response."""
    marker = "raw-secret-marker"

    with pytest.raises(QaResponseSchemaError) as raised:
        parse_single_json_object(f'{{"secret":"{marker}", invalid}}')

    error = raised.value
    assert marker not in str(error)
    assert marker not in repr(error)
    assert error.__cause__ is None
    assert error.__context__ is None
    traceback = error.__traceback__
    while traceback is not None:
        if traceback.tb_frame.f_code.co_filename.endswith("json_response.py"):
            assert marker not in repr(traceback.tb_frame.f_locals)
        traceback = traceback.tb_next


@pytest.mark.parametrize(
    "payload",
    [
        '{"number":1e10000}',
        '{"nested":{"values":[1e10000]}}',
        '{"integer":' + "9" * 400 + "}",
    ],
)
def test_json_parser_rejects_overflowing_numbers_at_any_depth(payload):
    """Overflowing JSON numbers must not cross the generic parser boundary."""
    with pytest.raises(QaResponseSchemaError):
        parse_single_json_object(payload)


def test_deep_json_raises_sanitized_schema_error_without_retaining_raw_text():
    """Deeply nested model output must not escape as RecursionError or leak raw text."""
    marker = "deep-json-secret"
    payload = '{"nested":' + "[" * 1100 + f'"{marker}"' + "]" * 1100 + "}"

    with pytest.raises(QaResponseSchemaError) as raised:
        parse_single_json_object(payload)

    error = raised.value
    assert error.__cause__ is None
    assert error.__context__ is None
    assert marker not in str(error)
    traceback = error.__traceback__
    while traceback is not None:
        if traceback.tb_frame.f_code.co_filename.endswith("json_response.py"):
            assert marker not in repr(traceback.tb_frame.f_locals)
        traceback = traceback.tb_next


def test_brackets_and_escaped_quotes_inside_json_string_do_not_count_as_depth():
    """Any future depth guard must remain aware of JSON string and escape syntax."""
    text = 'literal [[[ {{{ "quoted" }}} ]]]'

    assert parse_single_json_object(
        '{"text":"literal [[[ {{{ \\\"quoted\\\" }}} ]]]"}'
    ) == {"text": text}


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
    "decision",
    ["covered", "intentional_foreign", "ambiguous"],
)
def test_non_missing_verdict_rejects_claimed_missing_facts(decision):
    """Only missing_content may carry evidence that can authorize repair."""
    with pytest.raises(QaResponseSchemaError):
        OmissionVerdict.from_dict(_omission_payload(decision=decision))


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

    with pytest.raises(TypeError):
        RepairProposal.from_dict(payload)


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


def test_style_suggestion_can_never_claim_objective_status():
    """Subjective style feedback must never enter an objective repair path."""
    with pytest.raises(QaResponseSchemaError):
        LanguageIssue.from_dict(
            _language_issue_payload(category="style_suggestion", objective=True)
        )


def test_authorizing_schema_repr_never_contains_model_or_metadata_text():
    """Logging schema objects must not leak model response content."""
    verdict = OmissionVerdict.from_dict(
        _omission_payload(
            missing_facts=["missing-secret"],
            explanation="verdict-secret",
            metadata={"trace": "metadata-secret"},
        )
    )
    proposal = RepairProposal.from_dict(
        {
            "candidate_id": "candidate-1",
            "translated_fragment": "fragment-secret",
            "glossary_terms_used": [],
            "metadata": {"trace": "metadata-secret"},
        },
        expected_candidate_id="candidate-1",
    )
    issue = LanguageIssue.from_dict(
        _language_issue_payload(
            original_text="original-secret",
            replacement_text="replacement-secret",
            explanation="issue-secret",
            metadata={"trace": "metadata-secret"},
        )
    )

    combined_repr = repr((verdict, proposal, issue))
    for secret in (
        "missing-secret",
        "verdict-secret",
        "fragment-secret",
        "original-secret",
        "replacement-secret",
        "issue-secret",
        "metadata-secret",
    ):
        assert secret not in combined_repr


def test_huge_integer_confidence_raises_typed_schema_error():
    """Float conversion overflow must stay inside the typed schema boundary."""
    with pytest.raises(QaResponseSchemaError):
        OmissionVerdict.from_dict(_omission_payload(confidence=10**1000))
