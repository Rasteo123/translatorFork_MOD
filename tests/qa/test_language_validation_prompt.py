"""The validator is shown the original line, and now it is asked about it.

Measured on 65 chapters: of 58 automatic edits six were wrong, and the two that
did real damage were invisible to any check of the edit's shape.  «двенадцать
рук» became «четырнадцать» — the Chinese source says 十二隻手 — and the
deliberately broken «Моя не очень хорошо играть в видео.» («Me no game videos
much good.») was repaired into correct Russian.  Both answers were already in
the request: every replacement travels with its source line.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from gemini_translator.qa.language_validation import (
    LanguageBlock,
    LanguageQaRequest,
    LanguageRepairBatch,
    LanguageReplacement,
)
from gemini_translator.qa.llm import CancellationToken, QaModelSelection
from gemini_translator.qa.llm.language_repairer import LanguageRepairValidator
from gemini_translator.utils.epub_json import (
    build_html_document_model,
    build_translation_payload,
)


_PROMPTS = json.loads(
    (
        Path(__file__).parents[2]
        / "gemini_translator/config/translation_qa_prompts.json"
    ).read_text(encoding="utf-8")
)
_VALIDATION_PROMPT = "language_batch_validation_v3"
_SOURCE_LINE = "七人同時出聲，表達心意，站成一排，十二隻手緊緊連在一起。"


class _Client:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def complete_json(self, prompt, *, model, max_output_tokens, cancellation, purpose=""):
        self.prompts.append(prompt)
        return {"confirmed_issue_ids": [], "rejected_issue_ids": ["issue-1"]}


def _sent_prompt(*, with_source: bool) -> str:
    """Run one validation and return the prompt the model was actually sent."""
    document_model = build_html_document_model(
        "<p>Семеро заговорили одновременно; двенадцать рук крепко сплелись.</p>",
        document_id="chapter-1",
    )
    block_id = build_translation_payload(document_model)["blocks"][0]["id"]
    request = LanguageQaRequest(
        chapter_id="chapter-1",
        document_model=document_model,
        source_language="zh",
        target_language="ru",
        model=QaModelSelection("gemini", "qa-model"),
        cancellation=CancellationToken(),
        source_text_by_block={block_id: _SOURCE_LINE} if with_source else {},
    )
    batch = LanguageRepairBatch(
        "chapter-1",
        (
            LanguageReplacement(
                issue_id="issue-1",
                block_id=block_id,
                original_text="двенадцать рук",
                replacement_text="четырнадцать рук",
            ),
        )
    )
    client = _Client()
    asyncio.run(
        LanguageRepairValidator(client).validate_batch(
            request,
            (LanguageBlock(block_id, "Семеро заговорили одновременно."),),
            batch,
            document_model,
        )
    )
    return client.prompts[0]


def test_the_validator_asks_the_prompt_that_knows_about_the_source():
    """Версия промпта — ещё и ключ кэша: старые ответы не должны пережить правку."""
    assert _VALIDATION_PROMPT in _PROMPTS
    assert "language_batch_validation_v2" not in _PROMPTS

    prompt = _sent_prompt(with_source=True)

    assert prompt.startswith(_PROMPTS[_VALIDATION_PROMPT].split("<__QA_DATA_TAG__>")[0])


@pytest.mark.parametrize(
    ("subject", "expected"),
    [
        ("оригинал главнее грамматики", "source_text"),
        ("числа берутся из оригинала", "Числа"),
        ("нарочно ломаная речь остаётся ломаной", "ломан"),
        ("название в кавычках при родовом слове", "НЕ склоняется"),
    ],
)
def test_the_prompt_asks_about_everything_the_measurement_caught(subject, expected):
    """Каждый вопрос стоит в промпте из-за конкретной ложной правки на книге."""
    assert expected in _PROMPTS[_VALIDATION_PROMPT], subject


def test_the_original_line_reaches_the_model_with_the_replacement():
    """Спрашивать про оригинал бессмысленно, если оригинала нет в запросе."""
    prompt = _sent_prompt(with_source=True)

    assert f"source_text: {_SOURCE_LINE}" in prompt
    assert "before: двенадцать рук" in prompt
    assert "after: четырнадцать рук" in prompt


def test_a_block_without_a_source_line_still_validates():
    """Не у каждого блока есть пара в оригинале — это не повод падать."""
    prompt = _sent_prompt(with_source=False)

    assert "source_text:" not in prompt
    assert "before: двенадцать рук" in prompt
