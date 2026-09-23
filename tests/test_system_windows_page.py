# -*- coding: utf-8 -*-
"""Страница «Системные окна»: таблица кандидатов, настройки, применение."""

import json
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore, QtGui, QtWidgets

from gemini_translator.ui.pages.system_windows_page import SystemWindowsPage
from gemini_translator.ui.shell import ShellPage
from gemini_translator.utils.system_windows import scan_project


def _chapter(*paragraphs):
    body = "\n\n".join(f"<p>{text}</p>" for text in paragraphs)
    return f"<html><body>\n<h1>Глава</h1>\n\n{body}\n</body></html>\n"


def _project(root):
    project = root / "book"
    (project / "OEBPS").mkdir(parents=True)
    (project / "OEBPS/chapter1_translated_gemini.html").write_text(
        _chapter("Текст.", "[Динь! Одна]", "Ещё текст.", "[Хозяин: Цзян Юй]", "[Уровень: 3]"), encoding="utf-8",
    )
    (project / "OEBPS/chapter2_translated_gemini.html").write_text(_chapter("Только проза."), encoding="utf-8")
    translation_map = {
        "OEBPS/chapter1.xhtml": {"_translated_gemini.html": "OEBPS/chapter1_translated_gemini.html"},
        "OEBPS/chapter2.xhtml": {"_translated_gemini.html": "OEBPS/chapter2_translated_gemini.html"},
    }
    (project / "translation_map.json").write_text(json.dumps(translation_map), encoding="utf-8")
    return project


class SystemWindowsPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        import tempfile
        from pathlib import Path

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = _project(Path(self._tmp.name))

    def _page(self):
        page = SystemWindowsPage()
        self.addCleanup(page.close)
        return page

    def test_is_shell_page_with_title(self):
        page = self._page()
        self.assertIsInstance(page, ShellPage)
        self.assertEqual(page.get_page_title(), "Системные окна")

    def test_core_widgets_exist(self):
        page = self._page()
        for attr in (
            "project_edit", "scan_button", "triggers_edit", "single_check", "exclude_edit",
            "table", "preview", "apply_button", "strip_button", "progress_bar", "log_output",
            "colors_table",
        ):
            self.assertTrue(hasattr(page, attr), f"missing widget: {attr}")

    def test_scan_results_fill_the_table_with_windows_only(self):
        page = self._page()
        page.set_scan_results(scan_project(self.project))
        self.assertEqual(page.table.rowCount(), 2)
        self.assertEqual(page.table.item(0, 1).text(), "Глава")
        self.assertIn("Динь! Одна", page.table.item(0, 4).text())
        self.assertEqual(page.table.item(1, 3).text(), "2")

    def test_selected_candidates_follow_checkboxes(self):
        page = self._page()
        page.set_scan_results(scan_project(self.project))
        page.table.item(0, 0).setCheckState(page.table.item(0, 0).checkState().Unchecked)
        selections = page.selected_candidates()
        self.assertEqual(len(selections), 1)
        path, candidates = selections[0]
        self.assertTrue(path.endswith("chapter1_translated_gemini.html"))
        self.assertEqual([candidate.lines[0] for candidate in candidates], ["[Хозяин: Цзян Юй]"])

    def test_kind_combo_overrides_candidate_kind(self):
        page = self._page()
        page.set_scan_results(scan_project(self.project))
        combo = page.table.cellWidget(0, 2)
        combo.setCurrentIndex(combo.findData("achievement"))
        _, candidates = page.selected_candidates()[0]
        self.assertEqual(candidates[0].kind, "achievement")

    def test_detector_settings_come_from_fields(self):
        page = self._page()
        page.triggers_edit.setText("статус, навык ,")
        page.single_check.setChecked(False)
        page.exclude_edit.setText(r"прим\.")
        settings = page.detector_settings()
        self.assertEqual(settings.triggers, ("статус", "навык"))
        self.assertFalse(settings.single_bracketed)
        self.assertEqual(settings.exclude_pattern, r"прим\.")

    def test_templates_come_from_colors_table(self):
        page = self._page()
        row = page.colors_table.findItems("Достижение", QtCore.Qt.MatchFlag.MatchExactly)[0].row()
        page.colors_table.item(row, 1).setText("#112233")
        templates = page.templates()
        self.assertEqual(templates["achievement"]["border"], "#112233")
        self.assertEqual(templates["status"]["border"], "#4fc3f7")

    def test_preview_shows_selected_row(self):
        page = self._page()
        page.set_scan_results(scan_project(self.project))
        page.table.selectRow(1)
        self.assertIn("Хозяин", page.preview.toPlainText())

    def test_apply_selected_wraps_files_and_logs(self):
        page = self._page()
        page.set_scan_results(scan_project(self.project))
        with patch("gemini_translator.ui.pages.system_windows_page.QMessageBox.information"):
            page.apply_selected()
            self.assertTrue(page.worker.wait(10_000))
            QtWidgets.QApplication.processEvents()
        html = (self.project / "OEBPS/chapter1_translated_gemini.html").read_text(encoding="utf-8")
        self.assertIn('data-sys="notice"', html)
        self.assertIn('data-sys="status"', html)
        self.assertIn("Оформлено", page.log_output.toPlainText())

    def test_can_leave_false_while_worker_runs(self):
        page = self._page()

        class _FakeThread:
            def isRunning(self):
                return True

        page.worker = _FakeThread()
        with patch("gemini_translator.ui.pages.system_windows_page.QMessageBox.warning"):
            self.assertFalse(page.can_leave())


class SystemWindowsPreviewAndPaletteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        import tempfile
        from pathlib import Path

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = _project(Path(self._tmp.name))

    def _page(self):
        page = SystemWindowsPage()
        self.addCleanup(page.close)
        return page

    def _color_row(self, page, label):
        return page.colors_table.findItems(label, QtCore.Qt.MatchFlag.MatchExactly)[0].row()

    def test_preview_shows_samples_of_every_kind_without_selection(self):
        page = self._page()
        text = page.preview.toPlainText()
        for fragment in ("СТАТУС ПЕРСОНАЖА", "Благословение", "активирован", "ПОВЫШЕНИЕ УРОВНЯ", "НОВЫЙ ТИТУЛ"):
            self.assertIn(fragment, text)

    def test_preview_follows_color_edits(self):
        page = self._page()
        row = self._color_row(page, "Статус")
        page.colors_table.item(row, 2).setText("#abcdef")
        self.assertIn("#abcdef", page.preview.toHtml())

    def test_preview_returns_to_samples_when_table_is_cleared(self):
        page = self._page()
        page.set_scan_results(scan_project(self.project))
        page.table.selectRow(1)
        self.assertIn("Хозяин", page.preview.toPlainText())
        page.set_scan_results([])
        self.assertIn("СТАТУС ПЕРСОНАЖА", page.preview.toPlainText())

    def test_color_cell_double_click_opens_palette(self):
        page = self._page()
        row = self._color_row(page, "Достижение")
        with patch(
            "gemini_translator.ui.pages.system_windows_page.QColorDialog.getColor",
            return_value=QtGui.QColor("#123456"),
        ) as dialog:
            page._pick_color(row, 1)
        dialog.assert_called_once()
        self.assertEqual(page.colors_table.item(row, 1).text(), "#123456")
        self.assertEqual(page.templates()["achievement"]["border"], "#123456")

    def test_cancelled_palette_keeps_the_old_color(self):
        page = self._page()
        row = self._color_row(page, "Достижение")
        with patch(
            "gemini_translator.ui.pages.system_windows_page.QColorDialog.getColor",
            return_value=QtGui.QColor(),
        ):
            page._pick_color(row, 1)
        self.assertEqual(page.colors_table.item(row, 1).text(), "#ffd740")

    def test_type_cell_double_click_does_not_open_palette(self):
        page = self._page()
        with patch("gemini_translator.ui.pages.system_windows_page.QColorDialog.getColor") as dialog:
            page._pick_color(0, 0)
        dialog.assert_not_called()

    def test_color_cells_show_swatches_that_follow_the_text(self):
        page = self._page()
        item = page.colors_table.item(self._color_row(page, "Навык"), 1)
        self.assertFalse(item.icon().isNull())
        item.setText("#ff0000")
        image = item.icon().pixmap(8, 8).toImage()
        self.assertEqual(image.pixelColor(4, 4).name(), "#ff0000")

    def test_open_preview_in_browser_writes_exact_html(self):
        page = self._page()
        page.set_scan_results(scan_project(self.project))
        page.table.selectRow(1)
        with patch(
            "gemini_translator.ui.pages.system_windows_page.QDesktopServices.openUrl",
            return_value=True,
        ) as opened:
            path = page.open_preview_in_browser()
        opened.assert_called_once()
        with open(path, encoding="utf-8") as handle:
            html = handle.read()
        self.assertIn("Хозяин", html)
        self.assertIn('data-sys="achievement"', html)
        self.assertNotIn("data-sys-orig", html)
