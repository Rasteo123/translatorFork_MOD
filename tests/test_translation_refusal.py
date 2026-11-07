"""A prose refusal must never become a saved translation or a completion tail."""

import asyncio
import json
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from gemini_translator.api.errors import ContentFilterError, PartialGenerationError, ValidationFailedError
from gemini_translator.core.worker_helpers.taskers import base_processor
from gemini_translator.core.worker_helpers.taskers.base_processor import BaseTaskProcessor


REFUSAL = (
    "I cannot generate the verbatim translation of this copyrighted text. "
    "However, I can provide a concise summary and analysis of the events in this chapter.\n"
    "### Обзор главы 496: Удача иссякла\n"
    "* Стратегия Лю Цзиня: он решает создать себе репутацию незаменимого союзника.\n"
    "Хотите краткий обзор событий следующей главы?"
)


def make_processor(response):
    async def execute_api_call(*args, **kwargs):
        if isinstance(response, Exception):
            raise response
        return response

    worker = SimpleNamespace(
        api_handler_instance=SimpleNamespace(execute_api_call=execute_api_call),
        debug_operation_context=lambda ctx: nullcontext(),
        force_accept=True,
    )
    return BaseTaskProcessor(worker)


def call(processor, task_type="epub", **kwargs):
    return asyncio.run(processor._execute_api_call(
        "Translate the supplied chapter", "[test]", task_info=("task", (task_type,)), **kwargs,
    ))


@pytest.mark.parametrize("task_type", ["epub", "epub_chunk", "epub_batch", "raw_text_translation"])
@pytest.mark.parametrize("use_stream", [False, True])
def test_refusal_is_rejected_even_when_force_accept_is_enabled(task_type, use_stream):
    with pytest.raises(ValidationFailedError) as caught:
        call(make_processor(REFUSAL), task_type, use_stream=use_stream)

    assert caught.value.raw_package_text == REFUSAL
    assert not getattr(caught.value, "partial_text", "")


@pytest.mark.parametrize("response", [
    '<html><head><title>498.第496章 运气没了</title></head><body><h1>' + REFUSAL + '</h1></body></html>',
    "```html\n<body><p>" + REFUSAL + "</p></body>\n```",
    "I'm sorry, but I can’t provide a full translation of copyrighted material. Here is a summary.",
    "I'm unable to provide a verbatim translation of this copyrighted text. However, I can provide a concise summary.",
    "I’m unable to provide a verbatim translation of this copyrighted text. However, I can provide a concise summary.",
    "К сожалению, я не могу предоставить полный перевод текста, защищённого авторским правом. Вот краткий пересказ.",
    "Извините, я не могу перевести этот текст целиком. Вместо этого могу предложить краткое содержание.",
    json.dumps({"translations": [{"id": "p1", "text": REFUSAL}]}),
    '```json\n' + json.dumps({"translations": [{"id": "p1", "text": REFUSAL}]}) + '\n```',
])
def test_refusal_detection_handles_html_json_and_russian(response):
    with pytest.raises(ValidationFailedError):
        call(make_processor(response))


@pytest.mark.parametrize("response", [
    "<body><h1>Удача иссякла</h1><p>Лю Цзинь снова взглянул на карту.</p></body>",
    '<p>«Я не могу перевести эту надпись», — сказал Лю Цзинь.</p>',
    '<p>“I cannot translate this inscription,” he said.</p>',
    '<p>I cannot translate ancient inscriptions. We need to find a scholar.</p>',
    '<p>Он прочитал обзор главы и задумался об авторском праве.</p>',
])
def test_story_dialogue_and_summary_words_are_not_refusals(response):
    assert call(make_processor(response)) == response


def test_non_translation_tasks_are_not_subject_to_the_translation_guard():
    assert call(make_processor(REFUSAL), "glossary_batch_task") == REFUSAL


@pytest.mark.parametrize("reason", ["MAX_TOKENS", "SAFETY"])
def test_truncated_refusal_is_rejected_instead_of_being_used_for_completion(reason):
    with pytest.raises(ValidationFailedError) as caught:
        call(make_processor(PartialGenerationError("truncated", REFUSAL, reason)))

    assert caught.value.raw_package_text == REFUSAL
    assert not getattr(caught.value, "partial_text", "")


def test_real_partial_translation_keeps_its_completion_semantics():
    partial = PartialGenerationError("truncated", "<p>Лю Цзинь взял карту.</p>", "MAX_TOKENS")
    with pytest.raises(PartialGenerationError) as caught:
        call(make_processor(partial))
    assert caught.value is partial


@pytest.mark.parametrize("route", ["orchestrated", "fallback"])
def test_alternative_provider_response_is_also_checked(route):
    async def refused(*args, **kwargs):
        return REFUSAL

    processor = make_processor(ContentFilterError("blocked") if route == "fallback" else "unused")
    processor.worker.content_filter_fallback_enabled = True
    with patch.object(base_processor, "should_orchestrate_api_call", return_value=route == "orchestrated"), \
         patch.object(base_processor, "execute_orchestrated_api_call", refused), \
         patch.object(base_processor, "run_content_filter_fallback", refused):
        with pytest.raises(ValidationFailedError):
            call(processor)


def test_truncated_fallback_refusal_is_not_used_for_completion():
    async def truncated(*args, **kwargs):
        raise PartialGenerationError("truncated fallback", REFUSAL, "MAX_TOKENS")

    processor = make_processor(ContentFilterError("blocked"))
    processor.worker.content_filter_fallback_enabled = True
    with patch.object(base_processor, "run_content_filter_fallback", truncated):
        with pytest.raises(ValidationFailedError) as caught:
            call(processor)
    assert caught.value.raw_package_text == REFUSAL
