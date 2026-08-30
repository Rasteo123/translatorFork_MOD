"""Batched language QA: three chapter-level requests, never one per defect."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from pathlib import Path

import pytest

from gemini_translator.qa.language_validation import (
    LanguageBlock,
    LanguageQaRequest,
    LanguageQualityPipeline,
    LanguageRepairConflict,
    LanguageReplacement,
    LanguageRuleIssue,
    NamedEntitySpan,
    RussianNlpAnalysis,
    SyntaxCandidate,
    apply_language_replacements,
    auto_fix_refusal,
    chunk_blocks,
)
from gemini_translator.qa.llm import CancellationToken, QaModelSelection
from gemini_translator.qa.llm.schemas import LanguageIssue
from gemini_translator.qa.models import GlossaryPolicy, RelevantGlossaryTerm
from gemini_translator.utils.epub_json import (
    build_html_document_model,
    build_translation_payload,
    render_document_html,
)


_CASES = json.loads(
    (Path(__file__).parents[1] / "fixtures/qa/language_quality_cases.json").read_text(
        encoding="utf-8"
    )
)
_CHAPTER_HTML = (
    "<p>Это сделало его чувствовать себя одиноким.</p>"
    "<p>Он взял себе решение уйти.</p>"
    "<p>Она дала ему знать о приезде.</p>"
)


class RecordingClient:
    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses
        self.calls: list[str] = []
        self.prompts: dict[str, str] = {}

    async def complete_json(
        self,
        prompt: str,
        *,
        model: QaModelSelection,
        max_output_tokens: int,
        cancellation: CancellationToken,
        purpose: str = "",
    ) -> dict[str, object]:
        self.calls.append(purpose)
        self.prompts[purpose] = prompt
        response = self.responses.get(purpose)
        if isinstance(response, BaseException):
            raise response
        if response is None:
            raise AssertionError(f"unexpected purpose {purpose!r}")
        return deepcopy(response)  # type: ignore[return-value]


def _issue(index: int, block_id: str, original: str, replacement: str) -> dict:
    return {
        "issue_id": f"issue-{index}",
        "category": "calque",
        "block_id": block_id,
        "original_text": original,
        "replacement_text": replacement,
        "objective": True,
        "confidence": 0.93,
        "explanation": "Буквальная калька английской конструкции.",
    }


def _model() -> dict:
    return build_html_document_model(_CHAPTER_HTML, document_id="chapter-1")


def _blocks(model: dict) -> tuple[LanguageBlock, ...]:
    payload = build_translation_payload(model)
    return tuple(
        LanguageBlock(block["id"], block["inlines"][0]["text"])
        for block in payload["blocks"]
    )


def _request(model: dict | None = None, **overrides: object) -> LanguageQaRequest:
    values: dict[str, object] = {
        "chapter_id": "chapter-1",
        "document_model": model if model is not None else _model(),
        "source_language": "en",
        "target_language": "ru",
        "model": QaModelSelection("gemini", "qa-model"),
        "cancellation": CancellationToken(),
    }
    values.update(overrides)
    return LanguageQaRequest(**values)  # type: ignore[arg-type]


def _three_calque_responses(model: dict) -> dict[str, object]:
    blocks = _blocks(model)
    issues = [
        _issue(
            1,
            blocks[0].block_id,
            "сделало его чувствовать себя одиноким",
            "заставило его почувствовать себя одиноким",
        ),
        _issue(2, blocks[1].block_id, "взял себе решение", "принял решение"),
        _issue(3, blocks[2].block_id, "дала ему знать", "сообщила ему"),
    ]
    return {
        "language_diagnosis": {"issues": issues},
        "language_batch_correction": {
            "replacements": [
                {
                    "issue_id": issue["issue_id"],
                    "replacement_text": issue["replacement_text"],
                }
                for issue in issues
            ]
        },
        "language_batch_validation": {
            "confirmed_issue_ids": [issue["issue_id"] for issue in issues],
            "rejected_issue_ids": [],
        },
    }


def _check(client: RecordingClient, request: LanguageQaRequest | None = None, **kwargs):
    request = request if request is not None else _request()
    pipeline = LanguageQualityPipeline(client)
    return asyncio.run(pipeline.check_chapter(request, **kwargs))


def test_several_calques_use_three_chapter_level_requests():
    """One request per defect would multiply cost with the number of issues."""
    model = _model()
    client = RecordingClient(_three_calque_responses(model))

    result = _check(client, _request(model))

    assert client.calls == [
        "language_diagnosis",
        "language_batch_correction",
        "language_batch_validation",
    ]
    assert len(result.issues) == 3
    assert len(result.applied) == 3
    assert result.preview_model is not None
    rendered = render_document_html(result.preview_model)
    assert "заставило его почувствовать себя одиноким" in rendered
    assert "принял решение" in rendered
    assert "сообщила ему" in rendered
    assert "взял себе решение" not in rendered


def test_diagnosis_without_auto_fixable_issues_stops_after_one_request():
    """Nothing to fix must not cost a correction and a validation request."""
    model = _model()
    blocks = _blocks(model)
    suggestion = _issue(1, blocks[0].block_id, "Это сделало его", "Это заставило его")
    suggestion.update(
        {"category": "style_suggestion", "objective": False, "confidence": 0.6}
    )
    client = RecordingClient({"language_diagnosis": {"issues": [suggestion]}})

    result = _check(client, _request(model))

    assert client.calls == ["language_diagnosis"]
    assert result.applied == ()
    assert len(result.suggestions) == 1
    assert result.preview_model is None


def test_rejected_issues_stay_suggestions_and_are_not_applied():
    """A correction the validator did not confirm is a proposal, not an edit."""
    model = _model()
    responses = _three_calque_responses(model)
    responses["language_batch_validation"] = {
        "confirmed_issue_ids": ["issue-1"],
        "rejected_issue_ids": ["issue-2", "issue-3"],
    }
    client = RecordingClient(responses)

    result = _check(client, _request(model))

    assert [item.issue_id for item in result.applied] == ["issue-1"]
    assert {issue.issue_id for issue in result.suggestions} == {"issue-2", "issue-3"}
    rendered = render_document_html(result.preview_model)
    assert "заставило его почувствовать себя одиноким" in rendered
    assert "взял себе решение" in rendered


def test_language_tool_and_slovnet_hints_share_the_single_diagnosis_request():
    """External analyzers are evidence for one request, not requests of their own."""
    model = _model()
    blocks = _blocks(model)
    client = RecordingClient(_three_calque_responses(model))

    result = _check(
        client,
        _request(model),
        rule_candidates=(
            LanguageRuleIssue(
                blocks[1].block_id,
                "RU_COMPOUNDS",
                "Возможная ошибка согласования",
                "взял себе решение",
                ("принял решение",),
            ),
        ),
        nlp_analysis=RussianNlpAnalysis(
            entities=(NamedEntitySpan(blocks[0].block_id, "Тан Сань", "PER"),),
            syntax_candidates=(
                SyntaxCandidate(blocks[2].block_id, "дала ему знать", "unusual_syntax"),
            ),
        ),
    )

    diagnosis_prompt = client.prompts["language_diagnosis"]
    assert "RU_COMPOUNDS" in diagnosis_prompt
    assert "Тан Сань" in diagnosis_prompt
    assert "unusual_syntax" in diagnosis_prompt
    assert client.calls.count("language_diagnosis") == 1
    assert len(result.applied) == 3


def test_a_language_tool_rule_the_model_rejects_changes_nothing():
    """An unconfirmed external rule must never edit the text by itself."""
    model = _model()
    blocks = _blocks(model)
    client = RecordingClient({"language_diagnosis": {"issues": []}})

    result = _check(
        client,
        _request(model),
        rule_candidates=(
            LanguageRuleIssue(
                blocks[0].block_id,
                "FALSE_POSITIVE_RULE",
                "Ложное срабатывание",
                "Это сделало его",
                ("Это заставило его",),
            ),
        ),
    )

    assert client.calls == ["language_diagnosis"]
    assert result.applied == ()
    assert result.preview_model is None


def test_a_long_chapter_is_split_into_deterministic_chunks():
    """Chunking is the only allowed split, and issue IDs must stay disjoint."""
    blocks = tuple(
        LanguageBlock(f"b-{index}", "Предложение номер %d." % index)
        for index in range(10)
    )

    chunks = chunk_blocks(blocks, 60)

    assert chunk_blocks(blocks, 60) == chunks
    assert sum(len(chunk) for chunk in chunks) == len(blocks)
    assert [block.block_id for chunk in chunks for block in chunk] == [
        block.block_id for block in blocks
    ]
    assert all(chunk for chunk in chunks)
    assert len(chunks) > 1


def test_overlapping_replacements_are_refused_before_any_edit():
    """Two edits over the same characters cannot both be applied safely."""
    model = _model()
    blocks = _blocks(model)
    replacements = (
        LanguageReplacement("issue-1", blocks[0].block_id, "сделало его", "заставило его"),
        LanguageReplacement("issue-2", blocks[0].block_id, "его чувствовать", "его ощущать"),
    )

    with pytest.raises(LanguageRepairConflict):
        apply_language_replacements(model, replacements)


def test_stale_replacement_span_is_refused():
    """A span that no longer exists means the chapter moved under the batch."""
    model = _model()
    blocks = _blocks(model)

    with pytest.raises(LanguageRepairConflict):
        apply_language_replacements(
            model,
            (LanguageReplacement("issue-1", blocks[0].block_id, "нет такого", "есть"),),
        )


def test_replacements_never_touch_another_block():
    """A batch edit must stay inside the block its issue names."""
    model = _model()
    blocks = _blocks(model)
    updated = apply_language_replacements(
        model,
        (
            LanguageReplacement(
                "issue-1", blocks[1].block_id, "взял себе решение", "принял решение"
            ),
        ),
    )

    rendered = render_document_html(updated)
    assert "принял решение" in rendered
    assert "Это сделало его чувствовать себя одиноким." in rendered
    assert render_document_html(model) == _CHAPTER_HTML


@pytest.mark.parametrize("case", _CASES, ids=[case["name"] for case in _CASES])
def test_auto_fix_eligibility_matches_the_reviewed_case_corpus(case):
    """Each category decides auto-fix eligibility for a documented reason."""
    issue = LanguageIssue(
        issue_id="issue-1",
        category=case["issue"]["category"],
        block_id="b-0",
        original_text=case["issue"]["original_text"],
        replacement_text=case["issue"]["replacement_text"] or None,
        objective=case["issue"]["objective"],
        confidence=case["issue"]["confidence"],
        explanation="Случай из корпуса.",
    )
    entities = tuple(
        NamedEntitySpan("b-0", entity["text"], entity["category"])
        for entity in case.get("entities", ())
    )
    glossary = tuple(
        RelevantGlossaryTerm(
            term["original_term"],
            term["canonical_translation"],
            GlossaryPolicy(term["policy"]),
            1,
            index,
        )
        for index, term in enumerate(case.get("glossary", ()))
    )

    refusal = auto_fix_refusal(
        issue, case["block_text"], entities=entities, glossary=glossary
    )

    assert (refusal == "") is case["expected"]["auto_fixable"]
    assert refusal == case["expected"]["reason"]
