# -*- coding: utf-8 -*-
"""
Тест-маршрутизация на устранение дублирования из dups-gt_ui_dialogs_epub-08:

находка ui-dialogs-epub-consistency/design/13-async-initial-setup-duplicates —
EpubHtmlSelectorDialog._async_initial_setup дублировал тело
_async_stage_1_build_ui_if_needed вместо вызова.

До рефакторинга: _async_initial_setup строил UI/скрывал заглушку сам,
не вызывая _async_stage_1_build_ui_if_needed — mock.patch на последний
не фиксировал ни одного вызова (тест RED).
После рефакторинга: _async_initial_setup — это просто вызов
_async_stage_1_build_ui_if_needed() (тест GREEN).
"""
import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from gemini_translator.ui.dialogs.epub import EpubHtmlSelectorDialog


class AsyncInitialSetupRoutesThroughStage1Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _make_dialog(self):
        dialog = EpubHtmlSelectorDialog.__new__(EpubHtmlSelectorDialog)
        return dialog

    def test_initial_setup_calls_stage_1_build_ui(self):
        dialog = self._make_dialog()

        with mock.patch.object(
            EpubHtmlSelectorDialog, "_async_stage_1_build_ui_if_needed"
        ) as stage_1_mock:
            dialog._async_initial_setup()

        stage_1_mock.assert_called_once_with()

    def test_initial_setup_does_not_duplicate_ui_build_side_effects(self):
        """Характеризация: _async_initial_setup сам по себе не должен
        трогать _ui_is_built/виджеты напрямую - это ответственность
        _async_stage_1_build_ui_if_needed, вызванного один раз."""
        dialog = self._make_dialog()
        dialog._ui_is_built = False
        dialog.loading_label = mock.Mock()
        dialog.main_content_widget = mock.Mock()
        populate_calls = []
        dialog._populate_full_ui = lambda: populate_calls.append(1)

        with mock.patch(
            "gemini_translator.ui.dialogs.epub.QtWidgets.QApplication.processEvents"
        ), mock.patch(
            "gemini_translator.ui.dialogs.epub.QtCore.QTimer.singleShot"
        ) as timer_mock:
            dialog._async_initial_setup()

        self.assertEqual(populate_calls, [1], "UI должен строиться ровно один раз")
        self.assertTrue(dialog._ui_is_built)
        dialog.loading_label.setVisible.assert_called_once_with(False)
        dialog.main_content_widget.setVisible.assert_called_once_with(True)
        # Ровно одно планирование следующего шага (а не два, как было бы при
        # двойном запуске цепочки через бывший _start_data_loading_chain).
        timer_mock.assert_called_once_with(0, dialog._async_stage_2_get_filelist)

    def test_start_data_loading_chain_wrapper_is_gone(self):
        # После объединения цепочки запуска (finding 13 кластера epub-08)
        # обёртка _start_data_loading_chain осталась без вызывающих — мёртвый
        # код удалён, чтобы не появился второй путь запуска этапа 2.
        self.assertFalse(hasattr(EpubHtmlSelectorDialog, "_start_data_loading_chain"))


if __name__ == "__main__":
    unittest.main()
