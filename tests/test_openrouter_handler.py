import json
import unittest
import zipfile
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from gemini_translator.api import token_usage
from gemini_translator.api.errors import (
    ContentFilterError,
    PartialGenerationError,
    QrouteOutputLimitError,
    RateLimitExceededError,
    TemporaryRateLimitError,
)
from gemini_translator.api.handlers.openrouter import OpenRouterApiHandler
from gemini_translator.core.worker import DEFAULT_TASK_STREAM_MODE, UniversalWorker
from gemini_translator.core.worker_helpers.emerger_tasks import EmergencyTask
from gemini_translator.core.worker_helpers.error_analyzer import ErrorAnalyzer
from gemini_translator.core.worker_helpers.taskers.epub_chunk_processor import EpubChunkProcessor
from gemini_translator.core.worker_helpers.taskers.output_limit_recovery import translate_split_fragment
from gemini_translator.core.worker_helpers.taskers.raw_text_processor import RawTextProcessor


def _make_handler(model_config, provider_config=None):
    worker = SimpleNamespace(
        provider_config=provider_config or {"is_async": True},
        model_config=model_config,
    )
    return OpenRouterApiHandler(worker)


class OpenRouterHandlerTests(unittest.TestCase):
    def test_provider_extra_headers_are_added_to_requests(self):
        handler = _make_handler(
            {"id": "auto"},
            {
                "is_async": True,
                "extra_headers": {
                    "X-OmniRoute-No-Cache": "true",
                    "x-omniroute-no-memory": "true",
                    "x-omniroute-compression": "off",
                },
            },
        )
        handler.worker.api_key = "endpoint-key"

        headers = handler._build_request_headers()

        self.assertEqual(headers["Authorization"], "Bearer endpoint-key")
        self.assertEqual(headers["X-OmniRoute-No-Cache"], "true")
        self.assertEqual(headers["x-omniroute-no-memory"], "true")
        self.assertEqual(headers["x-omniroute-compression"], "off")

    def test_model_without_reasoning_config_leaves_payload_unchanged(self):
        handler = _make_handler({"id": "translator"})
        payload = {"model": "translator"}

        handler._apply_openai_reasoning_options(payload)

        self.assertEqual(payload, {"model": "translator"})

    def test_model_reasoning_effort_is_added_to_payload(self):
        handler = _make_handler({"id": "translator", "reasoning_effort": "high"})
        payload = {"model": "translator"}

        handler._apply_openai_reasoning_options(payload)

        self.assertEqual(payload["reasoning_effort"], "high")

    def test_provider_reasoning_effort_is_used_as_fallback(self):
        handler = _make_handler(
            {"id": "translator"},
            {"is_async": True, "default_reasoning_effort": "HIGH"},
        )
        payload = {"model": "translator"}

        handler._apply_openai_reasoning_options(payload)

        self.assertEqual(payload["reasoning_effort"], "high")

    def test_model_access_denied_error_is_detected(self):
        response_text = (
            '{"error":{"message":"model gemini-3.5-flash-extra-low is not allowed '
            'for this API key","type":"qroute_error"}}'
        )

        self.assertTrue(
            OpenRouterApiHandler._is_model_access_denied_error(403, response_text)
        )

    def test_generic_key_error_is_not_model_access_denied(self):
        response_text = '{"error":{"message":"invalid api key","type":"auth_error"}}'

        self.assertFalse(
            OpenRouterApiHandler._is_model_access_denied_error(403, response_text)
        )

    def test_model_access_denied_stream_error_retries_once_without_stream(self):
        response_text = (
            '{"error":{"message":"model gemini-3.5-flash-extra-low is not allowed '
            'for this API key","type":"qroute_error"}}'
        )

        self.assertTrue(
            OpenRouterApiHandler._should_retry_without_stream_for_model_access(
                403,
                response_text,
                use_stream=True,
                already_retried=False,
            )
        )
        self.assertFalse(
            OpenRouterApiHandler._should_retry_without_stream_for_model_access(
                403,
                response_text,
                use_stream=True,
                already_retried=True,
            )
        )
        self.assertFalse(
            OpenRouterApiHandler._should_retry_without_stream_for_model_access(
                403,
                response_text,
                use_stream=False,
                already_retried=False,
            )
        )


class _FakeResponse:
    def __init__(self, status, text="", headers=None, stream_lines=None, payload=None):
        self.status = status
        self._text = text
        self.headers = headers or {}
        self._lines = stream_lines or []
        self._payload = payload

    async def text(self):
        return self._text

    async def json(self, **kwargs):
        return self._payload

    @property
    def content(self):
        async def lines():
            for line in self._lines:
                yield line.encode("utf-8")

        return lines()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


class _FakeSession:
    def __init__(self, response):
        self._response = response
        self.requests = 0
        self.last_payload = None
        self.last_url = None

    def post(self, *args, **kwargs):
        self.requests += 1
        self.last_url = args[0]
        self.last_payload = kwargs.get("json")
        return self._response


def _make_calling_handler(response):
    worker = SimpleNamespace(
        provider_config={"is_async": True, "base_url": "http://127.0.0.1:20128/v1/chat/completions"},
        model_config={"id": "agy/gemini-3.8-flash-low", "max_output_tokens": 32768},
        api_key="endpoint-key",
        model_id="agy/gemini-3.8-flash-low",
        prompt_builder=SimpleNamespace(system_instruction=None),
        temperature_override_enabled=False,
        temperature=None,
        _post_event=lambda *a, **k: None,
    )
    handler = OpenRouterApiHandler(worker)
    handler.base_url = "http://127.0.0.1:20128/v1/chat/completions"
    handler.is_dynamic_local = False
    session = _FakeSession(response)

    async def _session():
        return session

    handler._get_or_create_session_internal = _session
    return handler


class QrouteSplitTranslationTests(unittest.IsolatedAsyncioTestCase):
    async def test_part_disconnect_cannot_supply_a_continuation_for_the_parent(self):
        worker = SimpleNamespace(_post_event=Mock())
        translate = AsyncMock(side_effect=[
            "<p>Первый перевод</p>",
            PartialGenerationError("connection lost", "текст середины главы", "NETWORK_ERROR"),
        ])

        with self.assertRaises(PartialGenerationError) as raised:
            await translate_split_fragment(
                worker, "<p>First</p><p>Second</p>", QrouteOutputLimitError(""), translate,
            )

        self.assertEqual(raised.exception.reason, "NETWORK_ERROR")
        self.assertEqual(raised.exception.partial_text, "")

    async def test_html_split_keeps_space_at_an_inline_tag_boundary(self):
        worker = SimpleNamespace(_post_event=Mock())
        source = "<body><p>Here are several words <em>with</em> more words in a row.</p></body>"

        async def translate(part, _attempt):
            return "<body>" + part.strip() + "</body>"

        result = await translate_split_fragment(worker, source, QrouteOutputLimitError(""), translate)

        self.assertEqual(result, source)

    async def test_capped_batch_returns_its_chapters_to_the_queue(self):
        task_info = ("task", ("epub_batch", "book.epub", ("Text/ch5.xhtml", "Text/ch6.xhtml")))
        worker = object.__new__(UniversalWorker)
        worker.worker_id = "worker"
        worker.task_manager = Mock()
        worker.task_manager.split_in_progress_batch_into_chapters.return_value = True
        worker._post_event = Mock()
        worker._execute_task = AsyncMock(side_effect=QrouteOutputLimitError(""))
        worker.error_analyzer = Mock()

        await worker._process_single_task_with_retries(task_info)

        worker.task_manager.split_in_progress_batch_into_chapters.assert_called_once_with(
            task_info, worker_id="worker", priority=1,
        )
        worker.error_analyzer.analyze_and_act.assert_not_called()

    async def test_raw_text_rebuilds_its_prompt_for_each_part(self):
        worker = SimpleNamespace(api_key="test", prompt_builder=SimpleNamespace(), _post_event=Mock())
        processor = RawTextProcessor(worker)
        processor._execute_api_call = AsyncMock(side_effect=[
            QrouteOutputLimitError("обрезано"), "первый", "второй",
        ])

        (task_info, translation), success, _, _ = await processor.execute(
            ("task", ("raw_text_translation", "first second", "Translate: {text}")),
        )

        self.assertTrue(success)
        self.assertEqual(task_info[0], "task")
        self.assertEqual(translation, "первый второй")
        prompts = [call.args[0].strip() for call in processor._execute_api_call.call_args_list]
        self.assertEqual(prompts, ["Translate: first second", "Translate: first", "Translate: second"])

    async def test_raw_text_split_preserves_the_translated_markup(self):
        worker = SimpleNamespace(api_key="test", prompt_builder=SimpleNamespace(), _post_event=Mock())
        processor = RawTextProcessor(worker)
        processor._execute_api_call = AsyncMock(side_effect=[
            QrouteOutputLimitError(""), "<p>Первый</p>", "<p>Второй</p>",
        ])

        (_, translation), success, _, _ = await processor.execute(
            ("task", ("raw_text_translation", "first second", "Translate into HTML: {text}")),
        )

        self.assertTrue(success)
        self.assertEqual(translation, "<p>Первый</p> <p>Второй</p>")

    async def test_capped_reserve_translates_smaller_parts_and_preserves_the_original_chunk(self):
        primary = _make_calling_handler(_text_response("", False, "content_filter"))
        primary.execute_api_call = primary.call_api
        primary_session = await primary._get_or_create_session_internal()
        requests = []
        context = {}

        @contextmanager
        def operation_context(value):
            original = context.copy()
            context.update(value)
            try:
                yield
            finally:
                context.clear()
                context.update(original)

        class ReserveSession:
            def post(self, _url, *, headers, json):
                requests.append(json)
                source = json["messages"][-1]["content"]
                if source.count("<p>") > 1:
                    return _text_response("", False, "length")
                return _text_response(source.replace("Source", "Перевод"), False, "stop")

        class ReserveHandler(OpenRouterApiHandler):
            execute_api_call = OpenRouterApiHandler.call_api

            def setup_client(self, *_args, **_kwargs):
                self.base_url = "https://ai.arbz.lol/v1/chat/completions"
                self.is_dynamic_local = False
                return True

            async def _get_or_create_session_internal(self):
                return ReserveSession()

        worker = SimpleNamespace(
            api_handler_instance=primary,
            content_filter_fallback_enabled=True,
            content_filter_fallback_provider="arbz",
            content_filter_fallback_model="plus/deepseek-v4.1-flash-1017",
            settings_manager=SimpleNamespace(
                load_key_statuses=lambda: [{"provider": "arbz", "key": "reserve-key"}],
                is_key_limit_active=lambda *_args: False,
            ),
            debug_operation_context=operation_context,
            get_debug_operation_context=lambda: context,
            context_manager=SimpleNamespace(prepare_html_for_translation=lambda text: text),
            prompt_builder=SimpleNamespace(
                system_instruction=None,
                _replace_media_with_placeholders=lambda text: text,
                prepare_for_api=lambda text_content, **_kwargs: (text_content, None, {}),
            ),
            response_parser=SimpleNamespace(
                _restore_media_from_placeholders=lambda translated_content, **_kwargs: translated_content,
            ),
            system_instruction="translate",
            _post_event=Mock(),
        )
        source = (
            '<body><section id="original"><p>Source one.</p><p>Source two.</p>'
            '<p>Source three.</p><p>Source four.</p></section></body>'
        )
        task_info = ("task", ("epub_chunk", "book.epub", "Text/ch5.xhtml", source, 2, 5, "", ""))

        with patch(
            "gemini_translator.core.worker_helpers.provider_orchestrator.get_api_handler_class",
            return_value=ReserveHandler,
        ):
            result, success, status, _ = await EpubChunkProcessor(worker).execute(task_info, use_stream=False)

        self.assertTrue(success)
        self.assertEqual(status, "SUCCESS")
        self.assertEqual(result[0], task_info)
        self.assertEqual(
            result[1]["translated_content"],
            '<body><section id="original"><p>Перевод one.</p><p>Перевод two.</p>'
            '<p>Перевод three.</p><p>Перевод four.</p></section></body>',
        )
        self.assertEqual(primary_session.requests, 1)
        self.assertEqual(len(requests), 7)
        self.assertTrue(all(item["max_tokens"] == 32768 for item in requests))


def _make_arbz_sol_handler(response, route="Codex"):
    handler = _make_calling_handler(response)
    config_path = Path(__file__).resolve().parents[1] / "config" / "api_providers.json"
    provider = json.loads(config_path.read_text(encoding="utf-8"))["arbz"]
    handler.worker.provider_config = provider
    handler.worker.model_config = provider["models"][f"GPT-6.2 Sol ({route})"]
    handler.setup_client(SimpleNamespace(api_key="endpoint-key"))
    return handler


class ArbzSolResponsesTests(unittest.IsolatedAsyncioTestCase):
    async def test_codex_posts_a_responses_request_and_reads_completed_text(self):
        result = {
            "status": "completed",
            "output": [
                {"type": "message", "phase": "commentary", "content": [{"type": "output_text", "text": "Planning…"}]},
                {"type": "reasoning", "summary": [{"type": "summary_text", "text": "Hidden"}]},
                {"type": "message", "content": [{"type": "output_text", "text": "Перевод"}]},
            ],
            "usage": {
                "input_tokens": 30,
                "input_tokens_details": {"cached_tokens": 10},
                "output_tokens": 6,
                "output_tokens_details": {"reasoning_tokens": 2},
                "total_tokens": 36,
            },
        }
        handler = _make_arbz_sol_handler(_FakeResponse(200, payload=result))
        handler.worker.prompt_builder.system_instruction = "Переводи."
        token = token_usage.begin_attempt()
        try:
            self.assertEqual(await handler.call_api("Translate", "log", use_stream=False, max_output_tokens=64), "Перевод")
            usage = token_usage.reported()
        finally:
            token_usage.end_attempt(token)

        session = await handler._get_or_create_session_internal()
        self.assertEqual(session.last_url, "https://ai.arbz.lol/v1/responses")
        self.assertEqual(session.last_payload, {
            "model": "gpt-6.2-sol", "input": "Translate", "instructions": "Переводи.",
            "max_output_tokens": 64, "stream": False, "store": False,
        })
        self.assertEqual(usage["cached_tokens"], 10)
        self.assertEqual(usage["thinking_tokens"], 2)

    async def test_codex_preserves_partial_text_when_responses_stream_ends_early(self):
        handler = _make_arbz_sol_handler(_FakeResponse(200, stream_lines=[
            'data: {"type":"response.output_text.delta","delta":"Начало"}\n',
        ]))

        with self.assertRaises(PartialGenerationError) as raised:
            await handler.call_api("Translate", "log")

        self.assertEqual(raised.exception.partial_text, "Начало")
        self.assertEqual(raised.exception.reason, "NETWORK_ERROR")

    async def test_plus_still_uses_chat_completions(self):
        handler = _make_arbz_sol_handler(_FakeResponse(200, payload={
            "choices": [{"message": {"content": "Перевод"}, "finish_reason": "stop"}],
        }), route="Plus")

        self.assertEqual(await handler.call_api("Translate", "log", use_stream=False), "Перевод")

        session = await handler._get_or_create_session_internal()
        self.assertEqual(session.last_url, "https://ai.arbz.lol/v1/chat/completions")
        self.assertEqual(session.last_payload["model"], "plus/gpt-6.2-sol")
        self.assertEqual(session.last_payload["messages"], [{"role": "user", "content": "Translate"}])


_OMNIROUTE_QUOTA_MESSAGE = (
    "Antigravity upstream error (429): You have exhausted your capacity on this "
    "model. Individual quota reached. Contact your administrator to enable overages."
)


class OpenRouterRateLimitTests(unittest.IsolatedAsyncioTestCase):
    """Окно квоты (5 часов у Antigravity) — это пауза, а не исчерпанный ключ.

    Раньше слово «quota» в теле любого ответа помечало ключ исчерпанным на
    сутки, и единственный ключ OmniRoute уводил сессию в тупик посреди книги.
    """

    async def test_429_with_reset_hint_pauses_for_the_hinted_time(self):
        body = (
            '{"error":{"message":"' + _OMNIROUTE_QUOTA_MESSAGE + '"},'
            '"retryAfterMs":900000}'
        )
        handler = _make_calling_handler(_FakeResponse(429, body))

        with self.assertRaises(TemporaryRateLimitError) as raised:
            await handler.call_api("prompt", "log", use_stream=False)

        self.assertEqual(raised.exception.delay_seconds, 900.0)

    async def test_429_pause_is_capped_at_one_hour(self):
        body = '{"error":{"message":"' + _OMNIROUTE_QUOTA_MESSAGE + '"},"retryAfterMs":7200000}'
        handler = _make_calling_handler(_FakeResponse(429, body))

        with self.assertRaises(TemporaryRateLimitError) as raised:
            await handler.call_api("prompt", "log", use_stream=False)

        self.assertEqual(raised.exception.delay_seconds, 3600.0)

    async def test_429_with_reset_days_away_exhausts_the_key(self):
        body = (
            '{"error":{"message":"Antigravity upstream error (429): You have exhausted '
            'your capacity on this model. Your quota will reset after 166h9m16s."}}'
        )
        handler = _make_calling_handler(_FakeResponse(429, body))

        with self.assertRaises(RateLimitExceededError) as raised:
            await handler.call_api("prompt", "log", use_stream=False)

        self.assertEqual(
            raised.exception.retry_after_seconds, 166 * 3600 + 9 * 60 + 16.0
        )
        self.assertIn("166", str(raised.exception))

    async def test_429_without_hint_keeps_the_short_default_pause(self):
        handler = _make_calling_handler(_FakeResponse(429, "Too Many Requests"))

        with self.assertRaises(TemporaryRateLimitError) as raised:
            await handler.call_api("prompt", "log", use_stream=False)

        self.assertEqual(raised.exception.delay_seconds, 20)

    async def test_429_retry_after_header_sets_the_pause(self):
        handler = _make_calling_handler(
            _FakeResponse(429, '{"error":{"message":"rate limit"}}', headers={"Retry-After": "45"})
        )

        with self.assertRaises(TemporaryRateLimitError) as raised:
            await handler.call_api("prompt", "log", use_stream=False)

        self.assertEqual(raised.exception.delay_seconds, 45.0)

    async def test_402_still_exhausts_the_key(self):
        handler = _make_calling_handler(_FakeResponse(402, '{"error":{"message":"Insufficient credits"}}'))

        with self.assertRaises(RateLimitExceededError):
            await handler.call_api("prompt", "log", use_stream=False)

    async def test_403_quota_text_still_exhausts_the_key(self):
        handler = _make_calling_handler(
            _FakeResponse(403, '{"error":{"message":"Daily quota exceeded for this key"}}')
        )

        with self.assertRaises(RateLimitExceededError):
            await handler.call_api("prompt", "log", use_stream=False)


def _stream(*chunks):
    return [f"data: {chunk}" for chunk in chunks] + ["data: [DONE]"]


def _text_response(text, use_stream, finish_reason="stop"):
    if not use_stream:
        return _FakeResponse(200, payload={
            "choices": [{"message": {"content": text}, "finish_reason": finish_reason}],
        })
    midpoint = len(text) // 2
    return _FakeResponse(200, stream_lines=_stream(
        json.dumps({"choices": [{"delta": {"content": text[:midpoint]}, "finish_reason": None}]}),
        json.dumps({"choices": [{"delta": {"content": text[midpoint:]}, "finish_reason": finish_reason}]}),
    ))


class ArbzGlossaryOutputLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_length_response_fails_glossary_batch_without_retry(self):
        response = _FakeResponse(
            200,
            stream_lines=_stream(
                '{"choices":[{"delta":{"reasoning_content":"Analyzing the chapter."},"finish_reason":null}]}',
                '{"choices":[{"delta":{},"finish_reason":"length"}]}',
            ),
            payload={
                "choices": [{
                    "message": {"content": "", "reasoning_content": "Analyzing the chapter."},
                    "finish_reason": "length",
                }],
                "usage": {"prompt_tokens": 25159, "completion_tokens": 8028},
            },
        )
        with TemporaryDirectory() as tmp:
            epub_path = Path(tmp) / "book.epub"
            with zipfile.ZipFile(epub_path, "w") as book:
                book.writestr("chapter.xhtml", "<html><body>A new character appears.</body></html>")
            task_info = ("task-id", ("glossary_batch_task", str(epub_path), ["chapter.xhtml"]))

            for use_stream in (True, False):
                with self.subTest(use_stream=use_stream):
                    handler = _make_calling_handler(response)
                    session = await handler._get_or_create_session_internal()
                    worker = UniversalWorker.__new__(UniversalWorker)
                    vars(worker).update(vars(handler.worker))
                    handler.worker = worker
                    worker.api_handler_instance = handler
                    worker.api_provider_name = "arbz"
                    worker.model_id = "gemini-4-hydrogen-high"
                    worker.model_config = {"id": worker.model_id, "max_output_tokens": 8192}
                    worker.is_cancelled = False
                    worker.worker_id = "worker-id"
                    worker.settings_manager = Mock()
                    worker.prompt_builder.prepare_for_glossary_generation = Mock(
                        return_value=("glossary prompt", None, None, {}, [])
                    )
                    worker.task_manager = Mock()
                    worker.task_manager.get_failure_history.return_value = {"total_count": 0, "errors": {}}
                    worker.task_manager._get_task_display_name.return_value = "Пакет глоссария"
                    worker.error_analyzer = ErrorAnalyzer(worker)
                    worker.emerger = EmergencyTask(worker)
                    worker._post_event = Mock()

                    with patch.dict(DEFAULT_TASK_STREAM_MODE, {"glossary_batch_task": use_stream}):
                        await worker._process_single_task_with_retries(task_info)

                    self.assertEqual(session.requests, 1)
                    worker.task_manager.task_failed_permanently.assert_called_once_with("worker-id", task_info)
                    worker.task_manager.task_requeued.assert_not_called()
                    worker.task_manager.task_requeued_for_retry.assert_not_called()
                    worker.task_manager.save_glossary_batch.assert_not_called()
                    self.assertNotIn("max_tokens", session.last_payload)
                    details = "\n".join(
                        args[1].get("details_text", "")
                        for args, _ in worker._post_event.call_args_list
                        if args[0] == "log_message"
                    )
                    self.assertIn("достигла лимита токенов", details)
                    self.assertIn("не вернула глоссарий", details)
                    self.assertIn("Причина: LENGTH", details)
                    self.assertNotIn("JSONDecodeError", details)

    async def test_only_arbz_glossary_skips_the_implicit_output_limit(self):
        for provider, task_type, max_output_tokens, expected_limit in (
            ("arbz", "glossary_batch_task", None, None),
            ("arbz", "glossary_batch_task", 16384, 16384),
            ("arbz", "epub_batch", None, 32768),
            ("arbz", "epub_chunk", None, 32768),
            ("arbz", "epub_chunk", 16384, 16384),
            ("openrouter", "glossary_batch_task", None, 8028),
        ):
            with self.subTest(provider=provider, task_type=task_type, explicit_limit=max_output_tokens):
                handler = _make_calling_handler(_text_response('{"Term": {', True, "stop"))
                handler.worker.api_provider_name = provider
                handler.worker.model_config["max_output_tokens"] = 8192
                handler.worker.get_debug_operation_context = lambda: {"task_type": task_type}
                session = await handler._get_or_create_session_internal()

                result = await handler.call_api(
                    "prompt", "log", allow_incomplete=True, max_output_tokens=max_output_tokens
                )

                self.assertEqual(result, '{"Term": {')
                if expected_limit is None:
                    self.assertNotIn("max_tokens", session.last_payload)
                else:
                    self.assertEqual(session.last_payload["max_tokens"], expected_limit)

    async def test_qroute_translation_reports_length_in_both_response_modes(self):
        for streaming, content in ((False, "truncated"), (True, "")):
            with self.subTest(streaming=streaming, content=content):
                handler = _make_calling_handler(_text_response(content, streaming, "length"))
                handler.worker.api_provider_name = "arbz"
                handler.worker.get_debug_operation_context = lambda: {"task_type": "epub_chunk"}

                with self.assertRaises(PartialGenerationError) as raised:
                    await handler.call_api("prompt", "log", use_stream=streaming, allow_incomplete=True)
                self.assertEqual(raised.exception.reason, "LENGTH")
                self.assertEqual(raised.exception.partial_text, content)


class OpenRouterContentFilterTests(unittest.IsolatedAsyncioTestCase):
    """Блокировка контента у OpenAI-совместимого шлюза приходит как
    ``finish_reason: "content_filter"`` (OmniRoute сводит к нему SAFETY,
    PROHIBITED_CONTENT, RECITATION и BLOCKLIST Gemini). Раньше обработчик
    смотрел только на ``length`` и отдавал обрезанный текст как готовый
    перевод, так что резервная модель не включалась.
    """

    async def test_stream_cut_by_content_filter_is_a_content_block_with_the_tail(self):
        handler = _make_calling_handler(
            _FakeResponse(
                200,
                stream_lines=_stream(
                    '{"choices":[{"delta":{"content":"Первый абзац."},"finish_reason":null}]}',
                    '{"choices":[{"delta":{},"finish_reason":"content_filter"}]}',
                ),
            )
        )

        with self.assertRaises(PartialGenerationError) as raised:
            await handler.call_api("prompt", "log", use_stream=True)

        self.assertEqual(raised.exception.reason, "CONTENT_FILTER")
        self.assertEqual(raised.exception.partial_text, "Первый абзац.")

    async def test_stream_blocked_before_any_text_is_a_content_filter_error(self):
        handler = _make_calling_handler(
            _FakeResponse(
                200,
                stream_lines=_stream('{"choices":[{"delta":{},"finish_reason":"content_filter"}]}'),
            )
        )

        with self.assertRaises(ContentFilterError):
            await handler.call_api("prompt", "log", use_stream=True)

    async def test_full_response_cut_by_content_filter_is_a_content_block_with_the_tail(self):
        handler = _make_calling_handler(
            _FakeResponse(
                200,
                payload={
                    "choices": [
                        {"message": {"content": "Первый абзац."}, "finish_reason": "content_filter"}
                    ]
                },
            )
        )

        with self.assertRaises(PartialGenerationError) as raised:
            await handler.call_api("prompt", "log", use_stream=False)

        self.assertEqual(raised.exception.reason, "CONTENT_FILTER")
        self.assertEqual(raised.exception.partial_text, "Первый абзац.")

    async def test_full_response_blocked_before_any_text_is_a_content_filter_error(self):
        handler = _make_calling_handler(
            _FakeResponse(
                200,
                payload={"choices": [{"message": {"content": ""}, "finish_reason": "content_filter"}]},
            )
        )

        with self.assertRaises(ContentFilterError):
            await handler.call_api("prompt", "log", use_stream=False)

    async def test_finished_stream_is_still_returned_whole(self):
        handler = _make_calling_handler(
            _FakeResponse(
                200,
                stream_lines=_stream(
                    '{"choices":[{"delta":{"content":"Весь текст."},"finish_reason":null}]}',
                    '{"choices":[{"delta":{},"finish_reason":"stop"}]}',
                ),
            )
        )

        self.assertEqual(await handler.call_api("prompt", "log", use_stream=True), "Весь текст.")


if __name__ == "__main__":
    unittest.main()
