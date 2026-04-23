import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

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
