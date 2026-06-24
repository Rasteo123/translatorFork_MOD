import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gemini_translator.api.errors import NetworkError, RateLimitExceededError, TemporaryRateLimitError
from gemini_translator.core.consistency_engine import FAST_PROOFREAD_MODE, ConsistencyEngine
from gemini_translator.ui.dialogs.consistency_checker import ConsistencyValidatorDialog


class _ButtonStub:
    def __init__(self, enabled):
        self.enabled = bool(enabled)

    def setEnabled(self, value):
        self.enabled = bool(value)


class _ProgressBarStub:
    def __init__(self, visible):
        self.visible = bool(visible)

    def setVisible(self, value):
        self.visible = bool(value)


class _RetrySettingsStub:
    def __init__(self):
        self.exhausted = []

    def is_key_limit_active(self, key_info, model_id):
        return False

    def load_proxy_settings(self):
        return None

    def increment_request_count(self, key_to_update, model_id):
        return True

    def mark_key_as_exhausted(self, key_to_mark, model_id):
        self.exhausted.append((key_to_mark, model_id))
        return True


class _ConsistencyDialogHarness:
    on_engine_error = ConsistencyValidatorDialog.on_engine_error
    on_error = ConsistencyValidatorDialog.on_error
    _restore_batch_fix_problem_map = ConsistencyValidatorDialog._restore_batch_fix_problem_map
    _on_batch_fix_error = ConsistencyValidatorDialog._on_batch_fix_error
    _release_power_inhibitor = ConsistencyValidatorDialog._release_power_inhibitor

    def __init__(
        self,
        *,
        analysis_running=False,
        fix_running=False,
        single_fix_running=False,
        single_fix_in_progress=False,
    ):
        self.logs = []
        self.batch_fix_updates = 0
        self.start_btn = _ButtonStub(False)
        self.stop_btn = _ButtonStub(True)
        self.select_chapters_btn = _ButtonStub(False)
        self.progress_bar = _ProgressBarStub(True)
        self._single_fix_in_progress = bool(single_fix_in_progress)
        self._thread_states = {
            "analysis_thread": bool(analysis_running),
            "fix_thread": bool(fix_running),
            "single_fix_thread": bool(single_fix_running),
        }
        self._batch_fix_original_problems_map = None

    def _log(self, message):
        self.logs.append(message)

    def _is_thread_running(self, thread_attr):
        return self._thread_states.get(thread_attr, False)

    def _update_batch_fix_button_state(self):
        self.batch_fix_updates += 1


class _SaveSessionHarness:
    _save_session = ConsistencyValidatorDialog._save_session

    def __init__(self, session_file, payload):
        self.session_file = Path(session_file)
        self.payload = payload
        self._restored_session_data = None

    def _build_session_payload(self):
        return self.payload


class ConsistencyResponseNormalizationTests(unittest.TestCase):
    def test_analysis_prompt_keeps_original_source_as_reference_only(self):
        engine = ConsistencyEngine(object())

        prompt = engine._build_analysis_prompt(
            [
                {
                    "name": "chapter_01.xhtml",
                    "content": "<p>Он вошёл в зал.</p>",
                    "source_content": "<p>他走进大厅。</p>",
                    "source_path": "Text/chapter_01.xhtml",
                }
            ],
            {},
        )

        self.assertIn("SOURCE ORIGINAL", prompt)
        self.assertIn("reference only", prompt)
        self.assertIn("TRANSLATED TEXT TO ANALYZE", prompt)
        self.assertIn("他走进大厅", prompt)
        self.assertIn("Он вошёл", prompt)

    def test_analysis_prompt_limits_original_source_chapters_per_request(self):
        engine = ConsistencyEngine(object())

        prompt = engine._build_analysis_prompt(
            [
                {
                    "name": "chapter_01.xhtml",
                    "content": "<p>Translated one.</p>",
                    "source_content": "<p>SOURCE ONE</p>",
                    "source_path": "Text/chapter_01.xhtml",
                },
                {
                    "name": "chapter_02.xhtml",
                    "content": "<p>Translated two.</p>",
                    "source_content": "<p>SOURCE TWO</p>",
                    "source_path": "Text/chapter_02.xhtml",
                },
            ],
            {"consistency_original_chapter_limit": 1},
        )

        self.assertIn("SOURCE ORIGINAL", prompt)
        self.assertIn("SOURCE ONE", prompt)
        self.assertNotIn("SOURCE TWO", prompt)
        self.assertIn("Translated two.", prompt)

    def test_source_reference_prompt_can_be_configured(self):
        engine = ConsistencyEngine(object())

        with tempfile.TemporaryDirectory() as temp_dir:
            prompts_path = Path(temp_dir) / "consistency_prompts.json"
            prompts_path.write_text(
                json.dumps(
                    {
                        "consistency_analysis": ["{chapters_text}"],
                        "source_reference": {
                            "intro": ["CUSTOM INTRO"],
                            "chapter": [
                                "CUSTOM CHAPTER {chapter_name}",
                                "CUSTOM PATH {source_path}",
                                "CUSTOM SUFFIX {source_path_suffix}",
                                "CUSTOM ORIGINAL {source_content}",
                                "CUSTOM TRANSLATED {translated_content}",
                            ],
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with patch("gemini_translator.api.config.get_resource_path", return_value=prompts_path):
                prompt = engine._build_analysis_prompt(
                    [
                        {
                            "name": "chapter_02.xhtml",
                            "content": "<p>Перевод</p>",
                            "source_content": "<p>原文</p>",
                            "source_path": "Text/chapter_02.xhtml",
                        }
                    ],
                    {},
                )

        self.assertIn("CUSTOM INTRO", prompt)
        self.assertIn("CUSTOM CHAPTER chapter_02.xhtml", prompt)
        self.assertIn("CUSTOM PATH Text/chapter_02.xhtml", prompt)
        self.assertIn("CUSTOM SUFFIX : Text/chapter_02.xhtml", prompt)
        self.assertIn("CUSTOM ORIGINAL <p>原文</p>", prompt)
        self.assertIn("CUSTOM TRANSLATED <p>Перевод</p>", prompt)
        self.assertNotIn("SOURCE ORIGINAL", prompt)

    def test_sanitize_fixed_chapter_rejects_new_latin_residue(self):
        engine = ConsistencyEngine(object())
        original = (
            "<html><body><p>"
            "\u0413\u0435\u0440\u043e\u0439 \u0432\u043e\u0448\u0435\u043b \u0432 \u0437\u0430\u043b."
            "</p></body></html>"
        )
        response = (
            "<html><body><p>"
            "The hero \u0432\u043e\u0448\u0435\u043b \u0432 \u0437\u0430\u043b."
            "</p></body></html>"
        )

        with self.assertRaisesRegex(ValueError, "untranslated Latin/CJK"):
            engine._sanitize_fixed_chapter_response(response, original)

    def test_sanitize_fixed_chapter_allows_existing_latin_residue(self):
        engine = ConsistencyEngine(object())
        original = (
            "<html><body><p>Status Window: "
            "\u0413\u0435\u0440\u043e\u0439 \u0432\u043e\u0448\u0435\u043b.</p></body></html>"
        )
        response = (
            "<html><body><p>Status Window: "
            "\u0413\u0435\u0440\u043e\u0439 \u0432\u043e\u0448\u0435\u043b \u0442\u0438\u0445\u043e."
            "</p></body></html>"
        )

        self.assertEqual(
            engine._sanitize_fixed_chapter_response(response, original),
            response,
        )

    def test_sanitize_fixed_chapter_rejects_new_cjk_residue(self):
        engine = ConsistencyEngine(object())
        original = "<p>\u041e\u043d \u043a\u0438\u0432\u043d\u0443\u043b.</p>"
        response = "<p>\u041e\u043d \u043a\u0438\u0432\u043d\u0443\u043b \u5934.</p>"

        with self.assertRaisesRegex(ValueError, "untranslated Latin/CJK"):
            engine._sanitize_fixed_chapter_response(response, original)

    def test_validate_response_normalizes_malformed_nested_fields(self):
        engine = ConsistencyEngine(object())
        raw_response = {
            "problems": {
                "type": "term_inconsistency",
                "confidence": "HIGH",
                "chapter": None,
            },
            "glossary_update": {
                "characters": [
                    "Alice",
                    {"name": "Bob", "aliases": "Bobby", "role": "mage"},
                ],
                "terms": "Mana",
                "plots": "Arc 1",
            },
            "context_summary": {
                "processed_chapters": "chapter_01.xhtml",
                "important_events": "Event 1",
                "next_chunk_focus": "Focus 1",
            },
        }

        validated = engine._validate_response(raw_response)
        engine.glossary_session.update_from_response(
            validated["glossary_update"],
            validated["context_summary"],
        )

        self.assertEqual(len(validated["problems"]), 1)
        self.assertEqual(validated["problems"][0]["confidence"], "high")
        self.assertEqual(validated["problems"][0]["chapter"], "Unknown")
        self.assertEqual(
            validated["glossary_update"]["characters"],
            [
                {"name": "Alice", "aliases": []},
                {"name": "Bob", "aliases": ["Bobby"], "role": "mage"},
            ],
        )
        self.assertEqual(
            validated["glossary_update"]["terms"],
            [{"term": "Mana", "definition": ""}],
        )
        self.assertEqual(validated["glossary_update"]["plots"], ["Arc 1"])
        self.assertEqual(
            validated["context_summary"],
            {
                "processed_chapters": ["chapter_01.xhtml"],
                "important_events": ["Event 1"],
                "next_chunk_focus": ["Focus 1"],
            },
        )
        self.assertEqual(
            engine.glossary_session.characters,
            [
                {"name": "Alice", "aliases": []},
                {"name": "Bob", "aliases": ["Bobby"], "role": "mage"},
            ],
        )
        self.assertEqual(
            engine.glossary_session.terms,
            [{"term": "Mana"}],
        )
        self.assertEqual(engine.glossary_session.processed_chapters, ["chapter_01.xhtml"])
        self.assertEqual(engine.glossary_session.important_events, ["Event 1"])
        self.assertEqual(engine.glossary_session.next_chunk_focus, ["Focus 1"])

    def test_fast_proofread_filter_keeps_only_gender_typos_and_meta_comments(self):
        engine = ConsistencyEngine(object())
        result = engine._filter_fast_proofread_result(
            {
                "problems": [
                    {"id": 10, "type": "gender_mismatch", "chapter": "1.xhtml"},
                    {"id": 11, "type": "typo", "chapter": "1.xhtml"},
                    {"id": 12, "type": "meta_comment", "chapter": "1.xhtml"},
                    {"id": 13, "type": "grammar", "chapter": "1.xhtml"},
                    {"id": 14, "type": "logic_error", "chapter": "1.xhtml"},
                    {"id": 15, "type": "term_inconsistency", "chapter": "1.xhtml"},
                ],
                "glossary_update": {
                    "characters": [{"name": "Alice"}],
                    "terms": [{"term": "Mana"}],
                },
                "context_summary": {
                    "processed_chapters": ["1.xhtml"],
                    "important_events": ["Story event"],
                    "next_chunk_focus": ["Focus"],
                },
            }
        )

        self.assertEqual(
            [problem["type"] for problem in result["problems"]],
            ["gender_mismatch", "typo", "meta_comment"],
        )
        self.assertEqual([problem["id"] for problem in result["problems"]], [1, 2, 3])
        self.assertEqual(result["glossary_update"], {"characters": [], "terms": []})
        self.assertEqual(
            result["context_summary"],
            {
                "processed_chapters": ["1.xhtml"],
                "important_events": [],
                "next_chunk_focus": [],
            },
        )

    def test_fast_proofread_prompt_labels_translated_text_without_source_reference(self):
        engine = ConsistencyEngine(object())

        prompt = engine._build_analysis_prompt(
            [
                {
                    "name": "chapter_01.xhtml",
                    "content": "<p>Translated only.</p>",
                    "path": "chapter_01.xhtml",
                }
            ],
            {"consistency_mode": FAST_PROOFREAD_MODE},
        )

        self.assertIn("### TRANSLATED TEXT TO ANALYZE", prompt)
        self.assertIn("--- CHAPTER: chapter_01.xhtml ---", prompt)
        self.assertIn("<p>Translated only.</p>", prompt)
        self.assertNotIn("SOURCE ORIGINAL", prompt)


class ConsistencyKeyRetryTests(unittest.TestCase):
    def test_analyze_chapters_discards_bad_key_and_retries_same_chunk(self):
        settings = _RetrySettingsStub()
        engine = ConsistencyEngine(settings)
        calls = []
        discarded = []
        logs = []

        def fake_call_api(prompt, config, api_key):
            calls.append(api_key)
            if api_key == "bad-key-123456":
                raise RateLimitExceededError(
                    "Ошибка доступа (403): Permission denied: Consumer "
                    "'api_key:bad-key-123456' has been suspended."
                )
            return (
                '{"problems":[],"glossary_update":{"characters":[],"terms":[]},'
                '"context_summary":{"processed_chapters":["chapter_01.xhtml"]}}'
            )

        engine._call_api = fake_call_api
        engine.key_discarded.connect(lambda key, reason: discarded.append((key, reason)))
        engine.log_message.connect(logs.append)

        active_keys = ["bad-key-123456", "good-key-123456"]
        engine.analyze_chapters(
            [{"name": "chapter_01.xhtml", "content": "Text", "path": "chapter_01.xhtml"}],
            {"chunk_size": 1, "provider": "gemini", "model": "gemini-2.0-flash-exp"},
            active_keys,
        )

        self.assertEqual(calls, ["bad-key-123456", "good-key-123456"])
        self.assertEqual(active_keys, ["good-key-123456"])
        self.assertEqual(discarded[0][0], "bad-key-123456")
        self.assertNotIn("bad-key-123456", discarded[0][1])
        self.assertTrue(settings.exhausted)
        self.assertEqual(engine.all_problems, [])
        self.assertTrue(any("Повтор анализа" in entry for entry in logs))

    def test_analyze_chapters_discards_invalid_token_and_retries_same_chunk(self):
        settings = _RetrySettingsStub()
        engine = ConsistencyEngine(settings)
        calls = []
        discarded = []

        def fake_call_api(prompt, config, api_key):
            calls.append(api_key)
            if api_key == "bad-token-123456":
                raise RateLimitExceededError("Неверный токен (…3456) DeepSeek.")
            return (
                '{"problems":[],"glossary_update":{"characters":[],"terms":[]},'
                '"context_summary":{"processed_chapters":["chapter_01.xhtml"]}}'
            )

        engine._call_api = fake_call_api
        engine.key_discarded.connect(lambda key, reason: discarded.append((key, reason)))

        active_keys = ["bad-token-123456", "good-token-123456"]
        engine.analyze_chapters(
            [{"name": "chapter_01.xhtml", "content": "Text", "path": "chapter_01.xhtml"}],
            {"chunk_size": 1, "provider": "deepseek", "model": "deepseek-chat"},
            active_keys,
        )

        self.assertEqual(calls, ["bad-token-123456", "good-token-123456"])
        self.assertEqual(active_keys, ["good-token-123456"])
        self.assertEqual(discarded[0][0], "bad-token-123456")
        self.assertTrue(settings.exhausted)
        self.assertEqual(engine.glossary_session.processed_chapters, ["chapter_01.xhtml"])

    def test_fix_chapter_discards_bad_key_and_retries_with_next_key(self):
        settings = _RetrySettingsStub()
        engine = ConsistencyEngine(settings)
        calls = []

        def fake_call_api(prompt, config, api_key):
            calls.append(api_key)
            if api_key == "bad-token-123456":
                raise RateLimitExceededError("Ошибка доступа (401): Неверный токен.")
            return "Text"

        engine._call_api = fake_call_api

        active_keys = ["bad-token-123456", "good-token-123456"]
        fixed = engine.fix_chapter(
            "Text",
            [{"type": "typo", "description": "Fix typo", "quote": "Text", "suggestion": "Text"}],
            {"provider": "deepseek", "model": "deepseek-chat"},
            active_keys,
            chapter_name="chapter_01.xhtml",
        )

        self.assertEqual(fixed, "Text")
        self.assertEqual(calls, ["bad-token-123456", "good-token-123456"])
        self.assertEqual(active_keys, ["good-token-123456"])
        self.assertTrue(settings.exhausted)

    def test_analyze_chapters_resume_skips_completed_chunk_and_keeps_saved_problems(self):
        settings = _RetrySettingsStub()
        engine = ConsistencyEngine(settings)
        calls = []
        chunks_done = []
        chapters = [
            {"name": "chapter_01.xhtml", "content": "Text 1", "path": "chapter_01.xhtml"},
            {"name": "chapter_02.xhtml", "content": "Text 2", "path": "chapter_02.xhtml"},
        ]
        saved_problem = {
            "id": "p1",
            "chapter": "chapter_01.xhtml",
            "type": "typo",
            "quote": "Text 1",
            "description": "saved",
        }
        config = {
            "chunk_size": 1,
            "provider": "gemini",
            "model": "gemini-2.0-flash-exp",
        }
        session_signature = engine.build_session_signature(chapters, config)
        resume_state = {
            "session_signature": session_signature,
            "glossary": {"characters": [], "terms": []},
            "processed_chapters": ["chapter_01.xhtml"],
            "problems": [saved_problem],
            "completed_chunks": {
                "analysis": [
                    ConsistencyEngine.chunk_resume_key([chapters[0]], session_signature)
                ],
            },
        }

        def fake_call_api(prompt, config, api_key):
            calls.append(prompt)
            self.assertIn("chapter_02.xhtml", prompt)
            return (
                '{"problems":[],"glossary_update":{"characters":[],"terms":[]},'
                '"context_summary":{"processed_chapters":["chapter_02.xhtml"]}}'
            )

        engine._call_api = fake_call_api
        engine.chunk_analyzed.connect(lambda result: chunks_done.append(result))

        engine.analyze_chapters(
            chapters,
            {
                **config,
                "_consistency_resume_state": resume_state,
            },
            ["good-key-123456"],
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(engine.all_problems, [saved_problem])
        self.assertEqual(
            engine.glossary_session.processed_chapters,
            ["chapter_01.xhtml", "chapter_02.xhtml"],
        )
        self.assertIn(
            ConsistencyEngine.chunk_resume_key([chapters[0]], session_signature),
            engine.get_completed_chunk_keys()["analysis"],
        )
        self.assertIn(
            ConsistencyEngine.chunk_resume_key([chapters[1]], session_signature),
            engine.get_completed_chunk_keys()["analysis"],
        )
        self.assertEqual(len(chunks_done), 1)

    def test_analyze_chapters_resume_does_not_skip_when_text_signature_changes(self):
        settings = _RetrySettingsStub()
        engine = ConsistencyEngine(settings)
        calls = []
        original_chapters = [
            {"name": "chapter_01.xhtml", "content": "Old text 1", "path": "chapter_01.xhtml"},
            {"name": "chapter_02.xhtml", "content": "Text 2", "path": "chapter_02.xhtml"},
        ]
        changed_chapters = [
            {"name": "chapter_01.xhtml", "content": "Changed text 1", "path": "chapter_01.xhtml"},
            {"name": "chapter_02.xhtml", "content": "Text 2", "path": "chapter_02.xhtml"},
        ]
        config = {
            "chunk_size": 1,
            "provider": "gemini",
            "model": "gemini-2.0-flash-exp",
        }
        saved_signature = engine.build_session_signature(original_chapters, config)
        resume_state = {
            "session_signature": saved_signature,
            "glossary": {"characters": [], "terms": []},
            "processed_chapters": ["chapter_01.xhtml"],
            "problems": [],
            "completed_chunks": {
                "analysis": [
                    ConsistencyEngine.chunk_resume_key([original_chapters[0]], saved_signature)
                ],
            },
        }

        def fake_call_api(prompt, config, api_key):
            calls.append(prompt)
            return (
                '{"problems":[],"glossary_update":{"characters":[],"terms":[]},'
                '"context_summary":{"processed_chapters":[]}}'
            )

        engine._call_api = fake_call_api

        engine.analyze_chapters(
            changed_chapters,
            {
                **config,
                "_consistency_resume_state": resume_state,
            },
            ["good-key-123456"],
        )

        self.assertEqual(len(calls), 2)
        self.assertIn("Changed text 1", calls[0])
        self.assertIn("chapter_02.xhtml", calls[1])

    def test_analyze_chapters_resume_does_not_skip_when_mode_signature_changes(self):
        settings = _RetrySettingsStub()
        engine = ConsistencyEngine(settings)
        calls = []
        chapters = [
            {"name": "chapter_01.xhtml", "content": "Text 1", "path": "chapter_01.xhtml"},
        ]
        saved_config = {
            "chunk_size": 1,
            "provider": "gemini",
            "model": "gemini-2.0-flash-exp",
        }
        fast_config = {
            **saved_config,
            "consistency_mode": FAST_PROOFREAD_MODE,
        }
        saved_signature = engine.build_session_signature(chapters, saved_config)
        resume_state = {
            "session_signature": saved_signature,
            "glossary": {"characters": [], "terms": []},
            "processed_chapters": ["chapter_01.xhtml"],
            "problems": [],
            "completed_chunks": {
                "analysis": [
                    ConsistencyEngine.chunk_resume_key([chapters[0]], saved_signature)
                ],
            },
        }

        def fake_call_api(prompt, config, api_key):
            calls.append(prompt)
            return (
                '{"problems":[{"id":1,"type":"typo","chapter":"chapter_01.xhtml"}],'
                '"glossary_update":{"characters":[],"terms":[]},'
                '"context_summary":{"processed_chapters":["chapter_01.xhtml"]}}'
            )

        engine._call_api = fake_call_api

        engine.analyze_chapters(
            chapters,
            {
                **fast_config,
                "_consistency_resume_state": resume_state,
            },
            ["good-key-123456"],
            mode=FAST_PROOFREAD_MODE,
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(len(engine.all_problems), 1)

    def test_analyze_chapters_resume_backfills_old_session_processed_chapters(self):
        settings = _RetrySettingsStub()
        engine = ConsistencyEngine(settings)
        calls = []
        chapters = [
            {"name": "chapter_01.xhtml", "content": "Text 1", "path": "chapter_01.xhtml"},
            {"name": "chapter_02.xhtml", "content": "Text 2", "path": "chapter_02.xhtml"},
        ]
        resume_state = {
            "glossary": {"characters": [], "terms": []},
            "processed_chapters": ["chapter_01.xhtml"],
            "problems": [],
        }

        def fake_call_api(prompt, config, api_key):
            calls.append(prompt)
            self.assertIn("chapter_02.xhtml", prompt)
            return (
                '{"problems":[],"glossary_update":{"characters":[],"terms":[]},'
                '"context_summary":{"processed_chapters":["chapter_02.xhtml"]}}'
            )

        engine._call_api = fake_call_api

        engine.analyze_chapters(
            chapters,
            {
                "chunk_size": 1,
                "provider": "gemini",
                "model": "gemini-2.0-flash-exp",
                "_consistency_resume_state": resume_state,
            },
            ["good-key-123456"],
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(
            engine.glossary_session.processed_chapters,
            ["chapter_01.xhtml", "chapter_02.xhtml"],
        )

    def test_analyze_chapters_retries_network_error_same_chunk(self):
        settings = _RetrySettingsStub()
        engine = ConsistencyEngine(settings)
        calls = []
        sleeps = []
        logs = []

        def fake_call_api(prompt, config, api_key):
            calls.append(api_key)
            if len(calls) == 1:
                raise NetworkError("Сервер Gemini перегружен (503).", delay_seconds=0)
            return (
                '{"problems":[],"glossary_update":{"characters":[],"terms":[]},'
                '"context_summary":{"processed_chapters":["chapter_01.xhtml"]}}'
            )

        engine._call_api = fake_call_api
        engine._sleep_for_retry = lambda delay: sleeps.append(delay)
        engine.log_message.connect(logs.append)

        active_keys = ["good-key-123456"]
        engine.analyze_chapters(
            [{"name": "chapter_01.xhtml", "content": "Text", "path": "chapter_01.xhtml"}],
            {
                "chunk_size": 1,
                "provider": "gemini",
                "model": "gemini-2.0-flash-exp",
                "transient_retry_limit": 2,
            },
            active_keys,
        )

        self.assertEqual(calls, ["good-key-123456", "good-key-123456"])
        self.assertEqual(active_keys, ["good-key-123456"])
        self.assertEqual(sleeps, [0])
        self.assertEqual(engine.glossary_session.processed_chapters, ["chapter_01.xhtml"])
        self.assertTrue(any("Чанк возвращён в работу" in entry for entry in logs))

    def test_analyze_chapters_retries_temporary_limit_without_discarding_key(self):
        settings = _RetrySettingsStub()
        engine = ConsistencyEngine(settings)
        calls = []
        sleeps = []

        def fake_call_api(prompt, config, api_key):
            calls.append(api_key)
            if len(calls) == 1:
                raise TemporaryRateLimitError("Временный лимит запросов (429).", delay_seconds=0)
            return (
                '{"problems":[],"glossary_update":{"characters":[],"terms":[]},'
                '"context_summary":{"processed_chapters":["chapter_01.xhtml"]}}'
            )

        engine._call_api = fake_call_api
        engine._sleep_for_retry = lambda delay: sleeps.append(delay)

        active_keys = ["good-key-123456"]
        engine.analyze_chapters(
            [{"name": "chapter_01.xhtml", "content": "Text", "path": "chapter_01.xhtml"}],
            {
                "chunk_size": 1,
                "provider": "gemini",
                "model": "gemini-2.0-flash-exp",
                "transient_retry_limit": 2,
            },
            active_keys,
        )

        self.assertEqual(calls, ["good-key-123456", "good-key-123456"])
        self.assertEqual(active_keys, ["good-key-123456"])
        self.assertEqual(sleeps, [0])
        self.assertFalse(settings.exhausted)

    def test_analyze_chapters_stops_instead_of_skipping_after_transient_retry_limit(self):
        settings = _RetrySettingsStub()
        engine = ConsistencyEngine(settings)
        calls = []
        errors = []

        def fake_call_api(prompt, config, api_key):
            calls.append(prompt)
            raise NetworkError("Сервер Gemini перегружен (503).", delay_seconds=0)

        engine._call_api = fake_call_api
        engine._sleep_for_retry = lambda delay: None
        engine.error_occurred.connect(errors.append)

        active_keys = ["good-key-123456"]
        engine.analyze_chapters(
            [
                {"name": "chapter_01.xhtml", "content": "Text 1", "path": "chapter_01.xhtml"},
                {"name": "chapter_02.xhtml", "content": "Text 2", "path": "chapter_02.xhtml"},
            ],
            {
                "chunk_size": 1,
                "provider": "gemini",
                "model": "gemini-2.0-flash-exp",
                "transient_retry_limit": 1,
            },
            active_keys,
        )

        self.assertEqual(len(calls), 2)
        self.assertTrue(engine.is_cancelled)
        self.assertEqual(engine.glossary_session.processed_chapters, [])
        self.assertTrue(any("Чанк не будет пропущен" in entry for entry in errors))


class ConsistencyDialogErrorHandlingTests(unittest.TestCase):
    def test_save_session_uses_temp_file_and_atomic_replace(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            session_file = Path(temp_dir) / "consistency_session.json"
            harness = _SaveSessionHarness(session_file, {"problems": [{"id": 1}]})
            real_replace = os.replace
            replace_calls = []

            def tracking_replace(src, dst):
                src_path = Path(src)
                dst_path = Path(dst)
                replace_calls.append((src_path, dst_path))
                self.assertTrue(src_path.exists())
                self.assertEqual(dst_path, session_file)
                real_replace(src_path, dst_path)

            with patch(
                "gemini_translator.ui.dialogs.consistency_checker.os.replace",
                side_effect=tracking_replace,
            ):
                harness._save_session()

            self.assertEqual(len(replace_calls), 1)
            self.assertTrue(replace_calls[0][0].name.startswith(".consistency_session.json."))
            self.assertEqual(json.loads(session_file.read_text(encoding="utf-8")), harness.payload)
            self.assertEqual(list(Path(temp_dir).glob(".consistency_session.json.*.tmp")), [])

    def test_save_session_failure_keeps_existing_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            session_file = Path(temp_dir) / "consistency_session.json"
            session_file.write_text('{"stable": true}\n', encoding="utf-8")
            harness = _SaveSessionHarness(session_file, {"bad": object()})

            harness._save_session()

            self.assertEqual(session_file.read_text(encoding="utf-8"), '{"stable": true}\n')
            self.assertEqual(list(Path(temp_dir).glob(".consistency_session.json.*.tmp")), [])

    def test_batch_fix_error_restores_original_problem_map(self):
        harness = _ConsistencyDialogHarness()
        original_map = {"chapter.xhtml": [{"id": "all"}]}
        filtered_map = {"chapter.xhtml": [{"id": "selected"}]}
        harness.engine = type("_EngineStub", (), {})()
        harness.engine.chapter_problems_map = filtered_map
        harness._batch_fix_original_problems_map = original_map

        harness._on_batch_fix_error("fatal boom")

        self.assertIs(harness.engine.chapter_problems_map, original_map)
        self.assertIsNone(harness._batch_fix_original_problems_map)
        self.assertEqual(len(harness.logs), 1)
        self.assertTrue(harness.logs[0].endswith(": fatal boom"))
        self.assertEqual(harness.batch_fix_updates, 1)

    def test_engine_error_keeps_stop_enabled_while_analysis_thread_is_running(self):
        harness = _ConsistencyDialogHarness(analysis_running=True)

        harness.on_engine_error("Ошибка анализа чанка 12: boom")

        self.assertEqual(harness.logs, ["❌ Ошибка: Ошибка анализа чанка 12: boom"])
        self.assertFalse(harness.start_btn.enabled)
        self.assertTrue(harness.stop_btn.enabled)
        self.assertFalse(harness.select_chapters_btn.enabled)
        self.assertTrue(harness.progress_bar.visible)
        self.assertEqual(harness.batch_fix_updates, 1)

    def test_worker_error_restores_controls(self):
        harness = _ConsistencyDialogHarness()

        harness.on_error("fatal boom")

        self.assertEqual(harness.logs, ["❌ Ошибка: fatal boom"])
        self.assertTrue(harness.start_btn.enabled)
        self.assertFalse(harness.stop_btn.enabled)
        self.assertTrue(harness.select_chapters_btn.enabled)
        self.assertFalse(harness.progress_bar.visible)
        self.assertEqual(harness.batch_fix_updates, 1)


if __name__ == "__main__":
    unittest.main()
