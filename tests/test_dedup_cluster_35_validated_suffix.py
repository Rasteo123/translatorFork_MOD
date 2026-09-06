"""dedup cluster-35: локальные литералы '_validated.html' в validation.py
должны идти через каноническую translation_versions.VALIDATED_SUFFIX.

(a) Характеризационный тест фиксирует значение канонической константы.
(b) Тесты-маршрутизаторы патчат gemini_translator.ui.dialogs.validation.
    VALIDATED_SUFFIX и убеждаются, что КАЖДОЕ из четырёх бывших мест с
    захардкоженным литералом ('_validated.html') в auto_process_good_files,
    on_selection_changed, apply_changes и
    _ensure_row_validated_content_loaded реально считывает эту константу
    из модуля, а не свою локальную копию. До рефакторинга (локальные
    литералы/переменные) эти тесты ПАДАЮТ, после — проходят.
"""

import os
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from gemini_translator.ui.dialogs import validation as validation_module
from gemini_translator.utils import translation_versions

_APP = QApplication.instance() or QApplication([])


class CanonicalValidatedSuffixValueTests(unittest.TestCase):
    """(a) Характеризация канонической реализации."""

    def test_canonical_suffix_value_is_validated_html(self):
        self.assertEqual(translation_versions.VALIDATED_SUFFIX, "_validated.html")


class EnsureRowValidatedContentRoutingTests(unittest.TestCase):
    """Маршрутизация для _ensure_row_validated_content_loaded (было: строка 5674:
    versions.get('_validated.html'))."""

    def _make_harness(self, tmpdir, versions):
        harness = types.SimpleNamespace()
        harness.results_data = {0: {"internal_html_path": "Text/ch1.xhtml"}}
        harness.validated_content_cache = {}
        harness.translated_folder = tmpdir
        fake_pm = MagicMock()
        fake_pm.get_versions_for_original.return_value = versions
        harness.project_manager = fake_pm
        harness._read_text_file = types.MethodType(
            validation_module.TranslationValidatorPage._read_text_file, harness
        )
        harness._ensure_row_validated_content_loaded = types.MethodType(
            validation_module.TranslationValidatorPage._ensure_row_validated_content_loaded,
            harness,
        )
        return harness

    def test_reads_version_keyed_by_canonical_constant(self):
        custom_suffix = "_CUSTOM_APPROVED_TEST.html"
        with tempfile.TemporaryDirectory() as tmp:
            rel_path = os.path.join("Text", "ch1" + custom_suffix)
            full_path = os.path.join(tmp, rel_path)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write("<p>одобрено</p>")

            harness = self._make_harness(tmp, {custom_suffix: rel_path})

            with patch.object(validation_module, "VALIDATED_SUFFIX", custom_suffix):
                content = harness._ensure_row_validated_content_loaded(0)

            self.assertEqual(content, "<p>одобрено</p>")


class OnSelectionChangedRoutingTests(unittest.TestCase):
    """Маршрутизация для on_selection_changed (было: строки 5288/5290:
    endswith('_validated.html') / '_validated.html' in versions)."""

    def _make_harness(self, path_for_row, versions):
        harness = types.SimpleNamespace()
        mock_item = MagicMock()
        mock_item.row.return_value = 0
        harness.table_results = MagicMock()
        harness.table_results.selectedItems.return_value = [mock_item]
        harness.table_results.rowCount.return_value = 3
        harness.btn_prev_item = MagicMock()
        harness.btn_next_item = MagicMock()
        harness.btn_toggle_code_view = MagicMock()
        harness.btn_toggle_compare = MagicMock()
        harness.view_original = MagicMock()
        harness.view_translated = MagicMock()
        harness.btn_save_changes = MagicMock()
        harness.is_comparing_validated = False
        harness.results_data = {
            0: {
                "path": path_for_row,
                "internal_html_path": "Text/ch1.xhtml",
                "is_edited": False,
            }
        }
        fake_pm = MagicMock()
        fake_pm.get_versions_for_original.return_value = versions
        harness.project_manager = fake_pm
        harness._update_translation_find_replace_state = MagicMock()
        harness.update_comparison_view = MagicMock()
        harness.on_selection_changed = types.MethodType(
            validation_module.TranslationValidatorPage.on_selection_changed, harness
        )
        return harness

    def test_shows_compare_button_for_version_keyed_by_canonical_constant(self):
        custom_suffix = "_CUSTOM_APPROVED_TEST.html"
        # Текущий файл строки НЕ является "готовой" версией (иное расширение),
        # но карта версий содержит готовую версию под кастомным суффиксом.
        harness = self._make_harness(
            path_for_row="Text/ch1_translated_dp.html",
            versions={custom_suffix: "Text/ch1" + custom_suffix},
        )

        with patch.object(validation_module, "VALIDATED_SUFFIX", custom_suffix):
            harness.on_selection_changed()

        harness.btn_toggle_compare.setVisible.assert_called_with(True)


class AutoProcessGoodFilesRoutingTests(unittest.TestCase):
    """Маршрутизация для auto_process_good_files (было: строка 5040:
    локальная VALIDATED_SUFFIX = "_validated.html")."""

    def test_renames_to_canonical_constant_value(self):
        custom_suffix = "_CUSTOM_APPROVED_TEST.html"
        with tempfile.TemporaryDirectory() as tmp:
            rel_path = os.path.join("Text", "ch1_translated_dp.html")
            source_path = os.path.join(tmp, rel_path)
            os.makedirs(os.path.dirname(source_path), exist_ok=True)
            with open(source_path, "w", encoding="utf-8") as f:
                f.write("<p>перевод</p>")

            harness = types.SimpleNamespace()
            fake_pm = MagicMock()
            fake_pm.get_all_originals.return_value = ["Text/ch1.xhtml"]
            fake_pm.get_versions_for_original.return_value = {
                "_translated_dp.html": rel_path,
            }
            harness.project_manager = fake_pm
            harness.translated_folder = tmp
            harness.results_data = {}
            harness.lbl_status = MagicMock()
            harness._is_destroyed = MagicMock(return_value=False)
            harness.start_analysis = MagicMock()
            harness.auto_process_good_files = types.MethodType(
                validation_module.TranslationValidatorPage.auto_process_good_files, harness
            )

            with patch.object(validation_module, "VALIDATED_SUFFIX", custom_suffix), \
                 patch.object(validation_module, "QMessageBox"):
                harness.auto_process_good_files()

            expected_dest = os.path.join(tmp, "Text", "ch1" + custom_suffix)
            self.assertTrue(
                os.path.exists(expected_dest),
                f"Файл не переименован в суффикс из VALIDATED_SUFFIX: {expected_dest}",
            )
            fake_pm.register_translation.assert_called_once()
            call_args = fake_pm.register_translation.call_args[0]
            self.assertEqual(call_args[0], "Text/ch1.xhtml")
            self.assertEqual(call_args[1], custom_suffix)


class ApplyChangesRoutingTests(unittest.TestCase):
    """Маршрутизация для apply_changes (было: строка 5413:
    локальная VALIDATED_SUFFIX = "_validated.html")."""

    def test_renames_ok_status_file_to_canonical_constant_value(self):
        custom_suffix = "_CUSTOM_APPROVED_TEST.html"
        with tempfile.TemporaryDirectory() as tmp:
            rel_path = os.path.join("Text", "ch1_translated_dp.html")
            source_path = os.path.join(tmp, rel_path)
            os.makedirs(os.path.dirname(source_path), exist_ok=True)
            with open(source_path, "w", encoding="utf-8") as f:
                f.write("<p>перевод</p>")

            harness = types.SimpleNamespace()
            fake_pm = MagicMock()
            harness.project_manager = fake_pm
            harness.translated_folder = tmp
            harness.results_data = {
                0: {
                    "internal_html_path": "Text/ch1.xhtml",
                    "status": "ok",
                    "path": source_path,
                }
            }
            harness.table_results = MagicMock()
            harness.table_results.rowCount.return_value = 0
            harness.check_show_all = MagicMock()
            harness.check_show_all.isChecked.return_value = True
            harness._are_any_translated_files_left = MagicMock(return_value=False)
            harness._sync_data_with_visual_order = MagicMock()
            harness._recalc_untranslated_stats_ui = MagicMock()
            harness._update_analyze_button_state = MagicMock()
            harness._populate_initial_table = MagicMock()
            harness.dirty_files = set()
            harness.apply_changes = types.MethodType(
                validation_module.TranslationValidatorPage.apply_changes, harness
            )

            with patch.object(validation_module, "VALIDATED_SUFFIX", custom_suffix), \
                 patch.object(validation_module, "QMessageBox"):
                harness.apply_changes()

            expected_dest = os.path.join(tmp, "Text", "ch1" + custom_suffix)
            self.assertTrue(
                os.path.exists(expected_dest),
                f"Файл не переименован в суффикс из VALIDATED_SUFFIX: {expected_dest}",
            )
            fake_pm.register_translation.assert_called_once()
            call_args = fake_pm.register_translation.call_args[0]
            self.assertEqual(call_args[0], "Text/ch1.xhtml")
            self.assertEqual(call_args[1], custom_suffix)


if __name__ == "__main__":
    unittest.main()
