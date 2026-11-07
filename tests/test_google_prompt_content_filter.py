"""Google's textual prompt rejection must follow the content-filter path."""

import asyncio
import json
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from gemini_translator.api.errors import ContentFilterError, PartialGenerationError
from gemini_translator.core.worker_helpers import provider_orchestrator as orchestrator
from gemini_translator.core.worker_helpers.taskers import base_processor
from gemini_translator.core.worker_helpers.taskers.base_processor import BaseTaskProcessor


GOOGLE_BLOCK = (
    "The prompt could not be submitted. The prompt contains sensitive words that violate Google's "
    "[Generative AI Prohibited Use policy](https://policies.google.com/terms/generative-ai/use-policy). "
    "Try rephrasing the prompt. If you think this was an error, "
    "[send feedback](https://aistudio.google.com/docs/troubleshooting)."
)
TRANSLATION = "<body><p>Лю Цзинь снова взглянул на карту.</p></body>"


def make_processor(response, *, fallback=False):
    async def execute_api_call(*args, **kwargs):
        if isinstance(response, Exception):
            raise response
        return response

    return BaseTaskProcessor(SimpleNamespace(
        api_handler_instance=SimpleNamespace(execute_api_call=execute_api_call),
        debug_operation_context=lambda ctx: nullcontext(),
        content_filter_fallback_enabled=fallback,
        force_accept=True,
    ))


def call(processor, task_type="epub", **kwargs):
    return asyncio.run(processor._execute_api_call(
        "Translate the supplied chapter", "[test]", task_info=("task", (task_type,)), **kwargs,
    ))


@pytest.mark.parametrize("task_type", ["epub", "epub_chunk", "epub_batch", "raw_text_translation"])
@pytest.mark.parametrize("use_stream", [False, True])
def test_google_prompt_rejection_is_a_content_filter_error(task_type, use_stream):
    with pytest.raises(ContentFilterError) as caught:
        call(make_processor(GOOGLE_BLOCK), task_type, use_stream=use_stream)

    assert caught.value.raw_package_text == GOOGLE_BLOCK
    assert not getattr(caught.value, "partial_text", "")


@pytest.mark.parametrize("response", [
    GOOGLE_BLOCK.replace("](", r"]\(").replace("Google's", "Google’s"),
    "```text\n" + GOOGLE_BLOCK + "\n```",
    '<html><body><p>' + GOOGLE_BLOCK.replace(
        "[Generative AI Prohibited Use policy](https://policies.google.com/terms/generative-ai/use-policy)",
        '<a href="https://policies.google.com/terms/generative-ai/use-policy">Generative AI Prohibited Use policy</a>',
    ) + '</p></body></html>',
    json.dumps({"translations": [{"id": "p1", "text": GOOGLE_BLOCK}]}),
])
def test_google_prompt_rejection_handles_response_wrappers(response):
    with pytest.raises(ContentFilterError) as caught:
        call(make_processor(response))
    assert caught.value.raw_package_text == response


@pytest.mark.parametrize("orchestrated", [False, True])
def test_google_prompt_rejection_triggers_configured_fallback(orchestrated):
    fallback = AsyncMock(return_value=TRANSLATION)
    with patch.object(base_processor, "should_orchestrate_api_call", return_value=orchestrated), \
         patch.object(base_processor, "execute_orchestrated_api_call", AsyncMock(return_value=GOOGLE_BLOCK)), \
         patch.object(base_processor, "run_content_filter_fallback", fallback):
        assert call(make_processor(GOOGLE_BLOCK, fallback=True), use_stream=True) == TRANSLATION

    fallback.assert_awaited_once()
    assert fallback.call_args.args[1] == "Translate the supplied chapter"
    assert fallback.call_args.kwargs["call_kwargs"] == {"use_stream": True}


def test_google_prompt_rejection_from_fallback_is_not_saved():
    fallback = AsyncMock(return_value=GOOGLE_BLOCK)
    with patch.object(base_processor, "run_content_filter_fallback", fallback):
        with pytest.raises(ContentFilterError) as caught:
            call(make_processor(ContentFilterError("blocked"), fallback=True))
    assert caught.value.raw_package_text == GOOGLE_BLOCK
    fallback.assert_awaited_once()


@pytest.mark.parametrize("reason", ["MAX_TOKENS", "SAFETY"])
@pytest.mark.parametrize("fallback_enabled", [False, True])
def test_partial_google_prompt_rejection_is_never_used_for_completion(reason, fallback_enabled):
    partial = PartialGenerationError("truncated", GOOGLE_BLOCK, reason)
    fallback = AsyncMock(return_value=TRANSLATION)
    with patch.object(base_processor, "run_content_filter_fallback", fallback):
        if fallback_enabled:
            assert call(make_processor(partial, fallback=True)) == TRANSLATION
            fallback.assert_awaited_once()
        else:
            with pytest.raises(ContentFilterError) as caught:
                call(make_processor(partial))
            assert caught.value.raw_package_text == GOOGLE_BLOCK
            fallback.assert_not_awaited()


@pytest.mark.parametrize("response", [
    TRANSLATION,
    '<p>Он прочитал Generative AI Prohibited Use policy и закрыл страницу.</p>',
    '<p>«' + GOOGLE_BLOCK + '» — прочитал он на экране.</p>',
    '<p>Google could not submit the prompt because of a network error.</p>',
])
def test_story_mentions_and_unrelated_errors_are_not_content_blocks(response):
    assert call(make_processor(response)) == response


@pytest.mark.parametrize("partial", [False, True])
def test_provider_attempt_does_not_report_google_prompt_rejection_as_success(partial):
    class BlockedHandler:
        def __init__(self, worker):
            pass

        def setup_client(self, client, proxy_settings=None):
            return True

        async def execute_api_call(self, prompt, log_prefix, **kwargs):
            if partial:
                raise PartialGenerationError("truncated", GOOGLE_BLOCK, "MAX_TOKENS")
            return GOOGLE_BLOCK

    attempt = orchestrator.ProviderAttempt(
        provider_id="test", model_name="Test", model_config={"id": "test"},
        api_key="test-key", label="primary",
    )
    worker = SimpleNamespace(worker_id="test", temperature=None, proxy_settings=None)
    with patch.object(orchestrator, "_provider_info", return_value={"handler_class": "BlockedHandler"}), \
         patch.object(orchestrator, "get_api_handler_class", return_value=BlockedHandler):
        result = asyncio.run(orchestrator._run_attempt(worker, attempt, "prompt", "[test]", {}))

    assert not result.ok
    assert isinstance(result.exception, ContentFilterError)
    assert result.exception.raw_package_text == GOOGLE_BLOCK
    assert result.text == ""
