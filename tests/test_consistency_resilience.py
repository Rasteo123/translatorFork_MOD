import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gemini_translator.api.errors import RateLimitExceededError
from gemini_translator.core.consistency_engine import ConsistencyEngine
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

    def _log(self, message):
        self.logs.append(message)

    def _is_thread_running(self, thread_attr):
        return self._thread_states.get(thread_attr, False)

    def _update_batch_fix_button_state(self):
        self.batch_fix_updates += 1


class ConsistencyResponseNormalizationTests(unittest.TestCase):
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


class ConsistencyDialogErrorHandlingTests(unittest.TestCase):
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
