"""
Интеграционный регресс на замечание рецензента (major) к находке
ui-dialogs-epub-consistency/bugs/4-epub-manager-on2-lcs-diff-bloc: первая
версия фикса срезала dp-таблицу LCS по грубому порогу n*m > MAX_LCS_CELLS,
который срабатывал уже на таблицах из ~1000 строк - то есть ровно в самом
частом сценарии (заменили один файл / досинхронизировали одну главу) вместо
точечного diff всегда выполнялась полная перерисовка (_full_table_rebuild),
которая принудительно сбрасывает чекбокс "включить в сборку" в True и
комбобокс версии на индекс 0 для КАЖДОЙ строки, а не только изменившейся.

Тест работает с настоящим QTableWidget (не моком): вручную снимает галочку
у одной строки в большой таблице, затем просит _smart_update_table
пересобрать таблицу с одним изменившимся путём в другом месте списка, и
проверяет, что галочка НЕ вернулась в True.

Второй тест проверяет соседнее замечание рецензента (minor): когда
изменения настолько велики, что после срезки общего префикса/суффикса
середина всё ещё превышает MAX_LCS_CELLS, полная перерисовка обязана уйти
в уже существующий чанковый путь (_chunked_fill), а не залить тысячи строк
синхронно на GUI-потоке одним вызовом.
"""

import os
import tempfile
import unittest
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore, QtWidgets

from gemini_translator.ui.dialogs.epub import TranslatedChaptersManagerDialog


def _make_project_manager(paths):
    pm = Mock()
    pm.get_all_originals.return_value = list(paths)
    pm.get_versions_for_original.side_effect = lambda p: {
        "_translated_gemini.html": p.replace(".xhtml", "_translated_gemini.html"),
    }
    return pm


class SurgicalUpdatePreservesRowStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _dialog(self, paths):
        dialog = TranslatedChaptersManagerDialog(
            tempfile.mkdtemp(),
            parent=None,
            original_epub_path=None,
            project_manager=_make_project_manager(paths),
        )
        self.addCleanup(dialog.deleteLater)
        return dialog

    def _drain_until_filled(self, dialog, limit=500):
        for _ in range(limit):
            self.app.processEvents()
            if not dialog._fill_in_progress and dialog.table.rowCount() > 0:
                last = dialog.table.rowCount() - 1
                if dialog.table.cellWidget(last, dialog.COL_FILE) is not None:
                    return
        self.fail("Таблица не заполнилась за отведённые тики")

    def test_unrelated_checkbox_survives_single_row_change(self):
        """Ключевой сценарий из находки: изменилась одна глава в большой
        таблице - чекбокс «включить» на ДРУГОЙ строке не должен сбрасываться."""
        n = 2000
        paths = [f"OEBPS/chapter{i}.xhtml" for i in range(n)]
        dialog = self._dialog(paths)
        self._drain_until_filled(dialog)
        self.assertEqual(dialog.table.rowCount(), n)

        # Пользователь вручную снял галочку с пятой строки.
        watched_row = 5
        item = dialog.table.item(watched_row, dialog.COL_INCLUDE)
        item.setCheckState(QtCore.Qt.CheckState.Unchecked)
        self.assertEqual(item.checkState(), QtCore.Qt.CheckState.Unchecked)

        # Целевой список отличается ровно одной главой на другом конце -
        # ни префикс, ни середина не задевают watched_row.
        new_paths = list(paths)
        new_paths[-1] = "OEBPS/chapter_renamed.xhtml"

        dialog._smart_update_table(new_paths)

        # Точечный diff синхронный - фоновой заливки не запускается.
        self.assertFalse(dialog._fill_in_progress)
        self.assertEqual(dialog.table.rowCount(), n)

        item_after = dialog.table.item(watched_row, dialog.COL_INCLUDE)
        self.assertEqual(
            item_after.checkState(),
            QtCore.Qt.CheckState.Unchecked,
            "точечный diff не должен трогать строки вне реального изменения - "
            "чекбокс «включить в сборку» должен остаться снятым",
        )

    def test_massive_change_falls_back_to_chunked_rebuild(self):
        """Когда середина diff'а после срезки префикса/суффикса всё ещё
        огромна, полная перерисовка обязана уйти в фон чанками, а не
        залить тысячи строк синхронно и не оставить таблицу/кнопку в
        промежуточном состоянии."""
        n = 1500
        paths = [f"OEBPS/old_chapter{i}.xhtml" for i in range(n)]
        dialog = self._dialog(paths)
        self._drain_until_filled(dialog)

        # Полностью другой список того же порядка величины - нет общего
        # префикса/суффикса, n*m после срезки намного больше MAX_LCS_CELLS.
        new_paths = [f"OEBPS/new_chapter{i}.xhtml" for i in range(n)]

        dialog._smart_update_table(new_paths)

        # Сразу после вызова заливка ушла в фон - кнопка сборки выключена,
        # таблица ещё не обязана быть полностью заполнена новыми путями.
        self.assertTrue(
            dialog._fill_in_progress,
            "такой массовый diff должен был уйти в чанковую перерисовку, "
            "а не выполниться синхронно одним вызовом",
        )
        self.assertFalse(dialog.create_epub_btn.isEnabled())

        self._drain_until_filled(dialog)

        self.assertFalse(dialog._fill_in_progress)
        self.assertTrue(dialog.create_epub_btn.isEnabled())
        self.assertEqual(dialog.table.rowCount(), n)
        first_item = dialog.table.item(0, dialog.COL_SOURCE)
        last_item = dialog.table.item(n - 1, dialog.COL_SOURCE)
        self.assertEqual(first_item.text(), "OEBPS/new_chapter0.xhtml")
        self.assertEqual(last_item.text(), f"OEBPS/new_chapter{n - 1}.xhtml")
        # Нумерация проставлена до конца самим _chunked_fill.
        self.assertEqual(dialog.table.item(n - 1, dialog.COL_NUMBER).text(), str(n))


if __name__ == "__main__":
    unittest.main()
