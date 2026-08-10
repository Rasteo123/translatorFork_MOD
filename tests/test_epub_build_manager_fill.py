import os
import tempfile
import unittest
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from gemini_translator.ui.dialogs.epub import TranslatedChaptersManagerDialog


def _make_project_manager(count):
    pm = Mock()
    pm.get_all_originals.return_value = [
        f"OEBPS/chapter{i}.xhtml" for i in range(count)
    ]
    pm.get_versions_for_original.side_effect = lambda p: {
        "_translated_gemini.html": p.replace(".xhtml", "_translated_gemini.html"),
    }
    return pm


class EpubBuildManagerFillTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _dialog(self, count):
        dialog = TranslatedChaptersManagerDialog(
            tempfile.mkdtemp(),
            parent=None,
            original_epub_path=None,
            project_manager=_make_project_manager(count),
        )
        self.addCleanup(dialog.deleteLater)
        return dialog

    def _drain_until_filled(self, dialog, limit=300):
        for _ in range(limit):
            self.app.processEvents()
            if not dialog._fill_in_progress and dialog.table.rowCount() > 0:
                last = dialog.table.rowCount() - 1
                if dialog.table.cellWidget(last, dialog.COL_FILE) is not None:
                    return
        self.fail("Таблица не заполнилась за отведённые тики")

    def test_construction_defers_loading(self):
        dialog = self._dialog(400)
        # Загрузка отложена: конструктор не строит 400 комбобоксов.
        self.assertEqual(dialog.table.rowCount(), 0)

    def test_chunked_fill_completes_and_reenables_build(self):
        dialog = self._dialog(400)
        self._drain_until_filled(dialog)
        self.assertEqual(dialog.table.rowCount(), 400)
        self.assertTrue(dialog.create_epub_btn.isEnabled())
        combo = dialog.table.cellWidget(0, dialog.COL_FILE)
        self.assertIsNotNone(combo)
        self.assertEqual(combo.count(), 1)
        # Нумерация проставлена до конца.
        self.assertEqual(dialog.table.item(399, dialog.COL_NUMBER).text(), "400")

    def test_build_button_disabled_while_filling(self):
        dialog = self._dialog(400)
        self.app.processEvents()  # стартовый тик: load_chapters + первый чанк
        if dialog._fill_in_progress:
            self.assertFalse(dialog.create_epub_btn.isEnabled())
        self._drain_until_filled(dialog)
        self.assertTrue(dialog.create_epub_btn.isEnabled())

    def test_small_projects_fill_synchronously(self):
        dialog = self._dialog(20)
        self.app.processEvents()  # отложенный load_chapters
        self.assertEqual(dialog.table.rowCount(), 20)
        self.assertFalse(dialog._fill_in_progress)


if __name__ == "__main__":
    unittest.main()
