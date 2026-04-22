import json
import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gemini_translator.core.consistency_engine import ConsistencyEngine
from gemini_translator.ui.dialogs.consistency_checker import ConsistencyValidatorDialog


class _ButtonStub:
    def __init__(self):
        self.enabled = False
        self.text = ""

    def setEnabled(self, value):
        self.enabled = bool(value)

    def setText(self, value):
        self.text = str(value)


class _GlossaryWidgetStub:
    def __init__(self, glossary):
        self._glossary = list(glossary)
        self.commit_calls = 0

    def commit_active_editor(self):
        self.commit_calls += 1

    def get_glossary(self):
        return list(self._glossary)


class _ParentStub:
    def __init__(self, parent=None, glossary_widget=None, output_folder=None):
        self._parent = parent
        self.glossary_widget = glossary_widget
        self.output_folder = output_folder

    def parent(self):
        return self._parent


class _ProjectManagerStub:
    def __init__(self, project_folder):
        self.project_folder = project_folder


class _ProblemsTableStub:
    def __init__(self):
        self.row_count = None

    def setRowCount(self, value):
        self.row_count = int(value)


class _ConsistencySharedGlossaryHarness:
    _find_shared_glossary_widget = ConsistencyValidatorDialog._find_shared_glossary_widget
    _normalize_shared_project_glossary_entries = staticmethod(
        ConsistencyValidatorDialog._normalize_shared_project_glossary_entries
    )
    _load_shared_project_glossary = ConsistencyValidatorDialog._load_shared_project_glossary
    _apply_shared_project_glossary = ConsistencyValidatorDialog._apply_shared_project_glossary
    _update_glossary_button_state = ConsistencyValidatorDialog._update_glossary_button_state
    _restore_session = ConsistencyValidatorDialog._restore_session

    def __init__(self, project_folder, *, parent=None):
        self._parent = parent
        self.project_manager = _ProjectManagerStub(project_folder)
        self.engine = ConsistencyEngine(object())
        self.glossary_btn = _ButtonStub()
        self.problems_table = _ProblemsTableStub()
        self.logs = []
        self.selected_chapter_ids = set()
        self.engine.all_problems = []
        self.engine.chapter_problems_map = {}

    def parent(self):
        return self._parent

    def _log(self, message):
        self.logs.append(str(message))

    def _set_selected_chapters(self, chapter_ids, fallback_to_all=False):
        self.selected_chapter_ids = set(chapter_ids or [])

    def on_chunk_done(self, result):
        self.logs.append(f"chunk:{len(result.get('problems', []))}")


class ConsistencySharedGlossaryTests(unittest.TestCase):
    def test_engine_imports_project_glossary_entries_into_terms(self):
        engine = ConsistencyEngine(object())

        engine.import_shared_glossary_entries([
            {"original": "Alice", "rus": "Алиса", "note": "главная героиня"},
            {"original": "Mana", "rus": "", "note": ""},
        ])

        self.assertEqual(
            engine.glossary_session.terms,
            [
                {"term": "Alice", "definition": "Алиса | главная героиня"},
                {"term": "Mana"},
            ],
        )

    def test_dialog_prefers_parent_glossary_widget_over_project_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_glossary_path = os.path.join(temp_dir, "project_glossary.json")
            with open(project_glossary_path, "w", encoding="utf-8") as fh:
                json.dump([{"original": "FileTerm", "rus": "Файл", "note": ""}], fh)

            glossary_widget = _GlossaryWidgetStub([
                {"original": "WidgetTerm", "rus": "Виджет", "note": "из перевода"}
            ])
            parent = _ParentStub(glossary_widget=glossary_widget)
            harness = _ConsistencySharedGlossaryHarness(temp_dir, parent=parent)

            glossary_entries = harness._load_shared_project_glossary()

            self.assertEqual(glossary_widget.commit_calls, 1)
            self.assertEqual(
                glossary_entries,
                [
                    {
                        "original": "WidgetTerm",
                        "rus": "Виджет",
                        "note": "из перевода",
                        "timestamp": None,
                    }
                ],
            )

    def test_restore_session_keeps_shared_project_glossary_terms(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_glossary_path = os.path.join(temp_dir, "project_glossary.json")
            with open(project_glossary_path, "w", encoding="utf-8") as fh:
                json.dump([{"original": "SharedTerm", "rus": "Общий перевод", "note": ""}], fh)

            harness = _ConsistencySharedGlossaryHarness(temp_dir)

            harness._restore_session(
                {
                    "selected_chapter_ids": [],
                    "glossary": {
                        "characters": [],
                        "terms": [{"term": "SessionTerm", "definition": "из сессии"}],
                    },
                    "processed_chapters": [],
                    "problems": [],
                }
            )

            self.assertEqual(
                harness.engine.glossary_session.terms,
                [
                    {"term": "SharedTerm", "definition": "Общий перевод"},
                    {"term": "SessionTerm", "definition": "из сессии"},
                ],
            )
            self.assertTrue(harness.glossary_btn.enabled)
            self.assertIn("2 терм.", harness.glossary_btn.text)
            self.assertIn("♻️ Сессия успешно восстановлена.", harness.logs)


if __name__ == "__main__":
    unittest.main()
