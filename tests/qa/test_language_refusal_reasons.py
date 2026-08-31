"""Every unapplied suggestion must say why, in the log a person actually reads."""

from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest

from gemini_translator.qa.language_validation import (
    REFUSAL_DESCRIPTIONS,
    LanguageQaRequest,
    LanguageQaResult,
    LanguageQualityPipeline,
    auto_fix_refusal,
    describe_refusal,
)
from gemini_translator.qa.llm import CancellationToken, QaModelSelection
from gemini_translator.qa.llm.schemas import LanguageIssue
from gemini_translator.qa.models import RiskLevel
from gemini_translator.qa.service import ChapterQaResult
from gemini_translator.utils.epub_json import (
    build_html_document_model,
    build_translation_payload,
)


_CHAPTER_HTML = (
    "<p>Он взял себе решение уйти.</p>"
    "<p>Она дала ему знать о приезде.</p>"
)


class _Client:
    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses

    async def complete_json(self, prompt, *, model, max_output_tokens, cancellation, purpose=""):
        response = self.responses.get(purpose)
        if isinstance(response, BaseException):
            raise response
        if response is None:
            raise AssertionError(f"unexpected purpose {purpose!r}")
        return deepcopy(response)


def _model() -> dict:
    return build_html_document_model(_CHAPTER_HTML, document_id="chapter-1")


def _block_ids(model: dict) -> list[str]:
    return [block["id"] for block in build_translation_payload(model)["blocks"]]


def _issue(index: int, block_id: str, original: str, replacement: str, **overrides) -> dict:
    payload = {
        "issue_id": f"issue-{index}",
        "category": "grammar",
        "block_id": block_id,
        "original_text": original,
        "replacement_text": replacement,
        "objective": True,
        "confidence": 0.95,
        "explanation": "Грамматическая ошибка.",
    }
    payload.update(overrides)
    return payload


def _request(model: dict, **overrides) -> LanguageQaRequest:
    values: dict[str, object] = {
        "chapter_id": "chapter-1",
        "document_model": model,
        "source_language": "en",
        "target_language": "ru",
        "model": QaModelSelection("gemini", "qa-model"),
        "cancellation": CancellationToken(),
        "auto_fix_categories": ("typo", "grammar", "punctuation"),
    }
    values.update(overrides)
    return LanguageQaRequest(**values)  # type: ignore[arg-type]


def _run(client: _Client, request: LanguageQaRequest) -> LanguageQaResult:
    return asyncio.run(LanguageQualityPipeline(client).check_chapter(request))


def test_a_local_refusal_names_its_gate_and_the_numbers_behind_it():
    """«Не применено» без причины выглядит как каприз; порог и уверенность — нет."""
    model = _model()
    blocks = _block_ids(model)
    diagnosis = {
        "issues": [
            _issue(1, blocks[0], "взял себе решение", "принял решение", confidence=0.80),
            _issue(
                2, blocks[1], "дала ему знать", "сообщила ему",
                category="calque", confidence=0.99,
            ),
        ]
    }
    client = _Client({"language_diagnosis": diagnosis})

    result = _run(client, _request(model))

    assert result.applied == ()
    assert result.refusals["issue-1"].startswith("low_confidence")
    assert "0.80" in result.refusals["issue-1"]
    assert result.refusals["issue-2"] == "category_not_auto_fixable"


def test_a_correction_stage_failure_marks_every_eligible_issue():
    """Сбой запроса — не молчаливое исчезновение правок, а имя сбоя у каждой."""
    model = _model()
    blocks = _block_ids(model)
    client = _Client(
        {
            "language_diagnosis": {
                "issues": [_issue(1, blocks[0], "взял себе решение", "принял решение")]
            },
            "language_batch_correction": TimeoutError("slow"),
        }
    )

    result = _run(client, _request(model))

    assert result.refusals["issue-1"] == "language_batch_correction_timeout"
    # The per-issue code stays a stable identity; the warning carries the cause.
    assert result.warnings[0].startswith("language_batch_correction_timeout")
    assert "slow" in result.warnings[0]


def test_a_validator_veto_is_recorded_per_issue():
    """Проверщик отклонил — именно это и должно быть написано напротив правки."""
    model = _model()
    blocks = _block_ids(model)
    issues = [
        _issue(1, blocks[0], "взял себе решение", "принял решение"),
        _issue(2, blocks[1], "дала ему знать", "сообщила ему"),
    ]
    client = _Client(
        {
            "language_diagnosis": {"issues": issues},
            "language_batch_correction": {
                "replacements": [
                    {"issue_id": issue["issue_id"], "replacement_text": issue["replacement_text"]}
                    for issue in issues
                ]
            },
            "language_batch_validation": {
                "confirmed_issue_ids": ["issue-1"],
                "rejected_issue_ids": ["issue-2"],
            },
        }
    )

    result = _run(client, _request(model))

    assert [replacement.issue_id for replacement in result.applied] == ["issue-1"]
    assert result.refusals == {"issue-2": "validation_declined"}
    assert [issue.issue_id for issue in result.suggestions] == ["issue-2"]


def test_every_generated_code_has_a_description_and_unknown_codes_survive():
    """Код без описания превратился бы в пустую строку в логе."""
    for code in REFUSAL_DESCRIPTIONS:
        assert describe_refusal(code) == REFUSAL_DESCRIPTIONS[code]
    assert describe_refusal("low_confidence (0.80 < 0.85)") == (
        REFUSAL_DESCRIPTIONS["low_confidence"] + " (0.80 < 0.85)"
    )
    assert describe_refusal("never_seen_before") == "never_seen_before"
    assert describe_refusal("") == ""


def _chapter_result(language: LanguageQaResult) -> ChapterQaResult:
    return ChapterQaResult(
        chapter_id="chapter-1",
        risk_level=RiskLevel.LOW,
        may_continue_translation=True,
        coverage_mode="semantic_alignment",
        language=language,
    )


def _suggestion_issue() -> LanguageIssue:
    return LanguageIssue(
        issue_id="issue-9",
        category="grammar",
        block_id="b-1",
        original_text="слишком преувеличивает",
        replacement_text="преувеличивает",
        objective=True,
        confidence=1.0,
        explanation="Плеоназм.",
    )


def test_the_log_details_show_the_reason_next_to_the_suggestion():
    """Ради этой строки всё и делалось: причина видна там, где читают правки."""
    language = LanguageQaResult(
        chapter_id="chapter-1",
        issues=(_suggestion_issue(),),
        suggestions=(_suggestion_issue(),),
        warnings=("language_batch_validation_failed",),
        refusals={"issue-9": "validation_declined"},
    )
    result = _chapter_result(language)

    text = result.change_details()
    html = result.change_details_html()

    assert "причина: модель-проверщик не подтвердила правку" in text
    assert "предложено: преувеличивает" in text
    assert "Предупреждения языковой проверки:" in text
    assert "проверка пакета исправлений не удалась" in text
    assert "не применено: модель-проверщик не подтвердила правку" in html
    assert "Предупреждения языковой проверки:" in html


def test_details_stay_intact_for_results_without_the_new_field():
    """Старый LanguageQaResult без refusals не должен ломать рендер."""
    language = LanguageQaResult(
        chapter_id="chapter-1",
        issues=(_suggestion_issue(),),
        suggestions=(_suggestion_issue(),),
    )
    result = _chapter_result(language)

    text = result.change_details()

    assert "Предложения без применения: 1" in text
    assert "причина:" not in text


# --- punctuation the author chose -------------------------------------------


@pytest.mark.parametrize(
    ("original", "replacement"),
    [
        # Measured on a real book: every one of these was applied automatically.
        ("Тан Юаню; слёзы", "Тан Юаню, слёзы"),
        ("глаз; в сердце", "глаз, в сердце"),
        ("бумаги: одна", "бумаги. Одна"),
        ("здание, они", "здание. Они"),
        # Not observed, but the same shape: straight quotes are left to the
        # person, because getting the direction of a guillemet wrong is worse
        # than leaving the quote alone.
        ('"реплика"', "«реплика»"),
    ],
)
def test_swapping_one_valid_mark_for_another_is_never_applied(original, replacement):
    """Точка с запятой вместо запятой — выбор того, кто писал фразу."""
    issue = LanguageIssue(
        issue_id="issue-1",
        category="punctuation",
        block_id="b-1",
        original_text=original,
        replacement_text=replacement,
        objective=True,
        confidence=1.0,
        explanation="Пунктуация.",
    )

    assert auto_fix_refusal(issue, f"Текст {original} дальше.") == "punctuation_rewrite"


@pytest.mark.parametrize(
    ("original", "replacement"),
    [
        # A mark that was missing, one that was extra, a case fix after a dash,
        # and a dash spelled the wrong way: all defects, all still applied.
        ("Благодаря тому что", "Благодаря тому, что"),
        ("слово ,и", "слово и"),
        ("— Спросил", "— спросил"),
        ("жизнь – будет", "жизнь — будет"),
        ("«Мама… – позвал", "«Мама… — позвал"),
    ],
)
def test_a_missing_extra_or_misspelled_mark_is_still_a_defect(original, replacement):
    """Забытая запятая и не то тире — это ошибки, а не стиль."""
    issue = LanguageIssue(
        issue_id="issue-1",
        category="punctuation",
        block_id="b-1",
        original_text=original,
        replacement_text=replacement,
        objective=True,
        confidence=1.0,
        explanation="Пунктуация.",
    )

    assert auto_fix_refusal(issue, f"Текст {original} дальше.") == ""


def test_the_rule_applies_only_to_punctuation():
    """Опечатка вправе поменять что угодно внутри слова."""
    issue = LanguageIssue(
        issue_id="issue-1",
        category="typo",
        block_id="b-1",
        original_text="скзал: он",
        replacement_text="сказал. Он",
        objective=True,
        confidence=1.0,
        explanation="Опечатка.",
    )

    assert auto_fix_refusal(issue, "Он скзал: он ушёл.") == ""


def test_the_refusal_explains_itself_in_the_log():
    assert describe_refusal("punctuation_rewrite") == (
        "замена одного знака препинания другим — это выбор автора"
    )


# --- a break the paragraph cannot hold ---------------------------------------


@pytest.mark.parametrize(
    ("original", "replacement"),
    [
        # Measured on a real book: both were applied, both inside one <p>, where
        # the break is only whitespace.  The first put an attribution dash in the
        # middle of one character's speech; the second changed nothing.
        ("возвращаться? Цянь Даолю", "возвращаться?\n— Цянь Даолю"),
        (
            "Прекрасная женщина сказала: — Этого я не знаю.",
            "Прекрасная женщина сказала:\n— Этого я не знаю.",
        ),
    ],
)
def test_a_replacement_may_not_ask_for_a_new_paragraph(original, replacement):
    """Разбить абзац замена внутри абзаца не может — только сделать вид."""
    issue = LanguageIssue(
        issue_id="issue-1",
        category="punctuation",
        block_id="b-1",
        original_text=original,
        replacement_text=replacement,
        objective=True,
        confidence=1.0,
        explanation="Пунктуация.",
    )

    assert auto_fix_refusal(issue, f"Текст {original} дальше.") == "paragraph_break"
    assert describe_refusal("paragraph_break") == (
        "правка просит разбить абзац — это делает человек"
    )
