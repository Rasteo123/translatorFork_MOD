import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from gemini_translator.ui.dialogs.validation import TranslationValidatorDialog
from gemini_translator.utils.validation_cache import build_snapshot_entry, build_text_hash


class _ComboStub:
    def __init__(self, value):
        self._value = value

    def currentData(self):
        return self._value


class _CheckStub:
    def __init__(self, checked=False):
        self._checked = checked

    def isChecked(self):
        return self._checked


class _ValidationHarness:
    _read_text_file = TranslationValidatorDialog._read_text_file
    _invalidate_analysis_for_data = TranslationValidatorDialog._invalidate_analysis_for_data
    _create_base_result_data = TranslationValidatorDialog._create_base_result_data
    _build_row_data_for_file = TranslationValidatorDialog._build_row_data_for_file
    _get_selected_analysis_mode = TranslationValidatorDialog._get_selected_analysis_mode
    _get_manual_excluded_paths = TranslationValidatorDialog._get_manual_excluded_paths
    _get_eligible_analysis_paths = TranslationValidatorDialog._get_eligible_analysis_paths
    _compute_analysis_targets = TranslationValidatorDialog._compute_analysis_targets

    def __init__(self):
        self.validation_snapshot_entries = {}
        self.validation_snapshot_available = False
        self.analysis_mode_combo = _ComboStub("all")
        self.check_revalidate_ok = _CheckStub(False)
        self.results_data = {}
        self.dirty_files = set()
        self.previous_problem_paths = set()


class _ProjectManagerStub:
    def __init__(self, project_folder, originals, versions_map):
        self.project_folder = project_folder
        self._originals = originals
        self._versions_map = versions_map

    def get_all_originals(self):
        return list(self._originals)

    def get_versions_for_original(self, original_path):
        return dict(self._versions_map.get(original_path, {}))


class _ProgressDialogStub:
    def __init__(self, *args, **kwargs):
        self.value = 0

    def setWindowModality(self, *args, **kwargs):
        return None

    def show(self):
        return None

    def wasCanceled(self):
        return False

    def setValue(self, value):
        self.value = value


class _MessageBoxStub:
    @staticmethod
    def warning(*args, **kwargs):
        return None


class ValidationReanalysisTests(unittest.TestCase):
    def test_build_row_data_restores_cached_snapshot_for_unchanged_file(self):
        harness = _ValidationHarness()

        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".html", delete=False) as temp_file:
            temp_file.write("<p>Hello world</p>")
            html_path = temp_file.name

        try:
            internal_path = "Text/chapter1.xhtml"
            content_hash = build_text_hash("<p>Hello world</p>")
            harness.validation_snapshot_available = True
            harness.validation_snapshot_entries[internal_path] = build_snapshot_entry(
                {
                    "len_orig": 12,
                    "len_trans": 15,
                    "ratio_value": 1.25,
                    "critical_reasons": ["Mismatch"],
                },
                content_hash,
            )

            data, needs_analysis = harness._build_row_data_for_file(
                internal_path,
                html_path,
                False,
            )

            self.assertFalse(needs_analysis)
            self.assertTrue(data["has_cached_analysis"])
            self.assertEqual(data["len_orig"], 12)
            self.assertEqual(data["critical_reasons"], ["Mismatch"])
            self.assertEqual(data["analyzed_content_hash"], content_hash)
        finally:
            os.remove(html_path)

    def test_build_row_data_marks_changed_file_for_reanalysis(self):
        harness = _ValidationHarness()

        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".html", delete=False) as temp_file:
            temp_file.write("<p>Updated content</p>")
            html_path = temp_file.name

        try:
            internal_path = "Text/chapter2.xhtml"
            harness.validation_snapshot_available = True
            harness.validation_snapshot_entries[internal_path] = build_snapshot_entry(
                {"len_orig": 10, "len_trans": 11},
                build_text_hash("<p>Older content</p>"),
            )

            data, needs_analysis = harness._build_row_data_for_file(
                internal_path,
                html_path,
                False,
            )

            self.assertTrue(needs_analysis)
            self.assertFalse(data["has_cached_analysis"])
            self.assertIsNotNone(data["current_content_hash"])
            self.assertEqual(
                data["analyzed_content_hash"],
                build_text_hash("<p>Older content</p>"),
            )
        finally:
            os.remove(html_path)

    def test_compute_analysis_targets_respects_modes_and_filters(self):
        harness = _ValidationHarness()
        harness.results_data = {
            0: {"internal_html_path": "a.xhtml", "status": "neutral", "is_validated_file": False},
            1: {"internal_html_path": "b.xhtml", "status": "neutral", "is_validated_file": False},
            2: {"internal_html_path": "c.xhtml", "status": "neutral", "is_validated_file": False},
            3: {"internal_html_path": "d.xhtml", "status": "ok", "is_validated_file": False},
            4: {"internal_html_path": "e.xhtml", "status": "neutral", "is_validated_file": True},
        }
        harness.dirty_files = {"a.xhtml", "c.xhtml", "e.xhtml"}
        harness.previous_problem_paths = {"b.xhtml", "c.xhtml", "d.xhtml", "e.xhtml"}

        harness.analysis_mode_combo = _ComboStub("all")
        self.assertEqual(harness._compute_analysis_targets(), {"a.xhtml", "b.xhtml", "c.xhtml"})
        self.assertEqual(
            harness._compute_analysis_targets(True),
            {"a.xhtml", "b.xhtml", "c.xhtml"},
        )
        self.assertEqual(harness._compute_analysis_targets("c.xhtml"), {"c.xhtml"})

        harness.analysis_mode_combo = _ComboStub("problematic")
        self.assertEqual(harness._compute_analysis_targets(), {"b.xhtml", "c.xhtml"})

        harness.analysis_mode_combo = _ComboStub("changed")
        self.assertEqual(harness._compute_analysis_targets(), {"a.xhtml", "c.xhtml"})

        harness.analysis_mode_combo = _ComboStub("problematic_or_changed")
        self.assertEqual(harness._compute_analysis_targets(), {"a.xhtml", "b.xhtml", "c.xhtml"})

        harness.check_revalidate_ok = _CheckStub(True)
        self.assertEqual(
            harness._compute_analysis_targets(),
            {"a.xhtml", "b.xhtml", "c.xhtml", "e.xhtml"},
        )

    def test_invalidate_analysis_for_data_clears_cached_problem_markers(self):
        harness = _ValidationHarness()
        data = {
            "translated_html": "<p>Fresh text</p>",
            "has_cached_analysis": True,
            "analyzed_content_hash": "old",
            "critical_reasons": ["Mismatch"],
            "structural_errors": {"missing_tags": ["p"]},
            "untranslated_words": ["hero"],
            "ratio_value": 0.5,
        }

        harness._invalidate_analysis_for_data(data)

        self.assertFalse(data["has_cached_analysis"])
        self.assertIsNone(data["analyzed_content_hash"])
        self.assertEqual(data["current_content_hash"], build_text_hash("<p>Fresh text</p>"))
        self.assertNotIn("critical_reasons", data)
        self.assertNotIn("structural_errors", data)
        self.assertNotIn("untranslated_words", data)
        self.assertNotIn("ratio_value", data)

    def test_consistency_check_skips_unreadable_chapter_without_name_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            internal_path = "Text/chapter1.xhtml"
            rel_path = "translated/chapter1.xhtml"
            full_path = os.path.join(temp_dir, rel_path)

            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w", encoding="utf-8") as temp_file:
                temp_file.write("<p>Hello</p>")

            fake_dialog_module = types.ModuleType("gemini_translator.ui.dialogs.consistency_checker")

            class _ConsistencyDialogStub:
                def __init__(self, *args, **kwargs):
                    self.args = args
                    self.kwargs = kwargs

                def exec(self):
                    return 0

            fake_dialog_module.ConsistencyValidatorDialog = _ConsistencyDialogStub

            harness = type("ConsistencyHarness", (), {})()
            harness.settings_manager = object()
            harness.project_manager = _ProjectManagerStub(
                temp_dir,
                [internal_path],
                {internal_path: {"": rel_path}},
            )

            real_open = open

            def _failing_open(path, *args, **kwargs):
                if os.path.abspath(path) == os.path.abspath(full_path):
                    raise OSError("boom")
                return real_open(path, *args, **kwargs)

            with patch.dict(sys.modules, {fake_dialog_module.__name__: fake_dialog_module}), \
                 patch("gemini_translator.ui.dialogs.validation.QProgressDialog", _ProgressDialogStub), \
                 patch("gemini_translator.ui.dialogs.validation.QMessageBox", _MessageBoxStub), \
                 patch("builtins.open", _failing_open):
                TranslationValidatorDialog._on_consistency_check(harness)

    def test_consistency_check_uses_unsaved_in_memory_translation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            internal_path = "Text/chapter1.xhtml"
            rel_path = "translated/chapter1.xhtml"
            full_path = os.path.join(temp_dir, rel_path)

            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w", encoding="utf-8") as temp_file:
                temp_file.write("<p>stale hero</p>")

            captured = {}
            fake_dialog_module = types.ModuleType("gemini_translator.ui.dialogs.consistency_checker")

            class _ConsistencyDialogStub:
                def __init__(self, chapters, *args, **kwargs):
                    captured["chapters"] = chapters

                def exec(self):
                    return 0

            fake_dialog_module.ConsistencyValidatorDialog = _ConsistencyDialogStub

            harness = type("ConsistencyHarness", (), {})()
            harness.settings_manager = object()
            harness.project_manager = _ProjectManagerStub(
                temp_dir,
                [internal_path],
                {internal_path: {"": rel_path}},
            )
            harness.results_data = {
                0: {
                    "internal_html_path": internal_path,
                    "path": full_path,
                    "translated_html": "<p>fixed in memory</p>",
                    "is_edited": True,
                }
            }

            with patch.dict(sys.modules, {fake_dialog_module.__name__: fake_dialog_module}), \
                 patch("gemini_translator.ui.dialogs.validation.QProgressDialog", _ProgressDialogStub), \
                 patch("gemini_translator.ui.dialogs.validation.QMessageBox", _MessageBoxStub):
                TranslationValidatorDialog._on_consistency_check(harness)

            self.assertEqual(captured["chapters"][0]["content"], "<p>fixed in memory</p>")


if __name__ == "__main__":
    unittest.main()
