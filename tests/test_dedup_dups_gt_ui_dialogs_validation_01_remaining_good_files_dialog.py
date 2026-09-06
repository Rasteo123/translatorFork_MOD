# -*- coding: utf-8 -*-
"""dups-gt_ui_dialogs_validation-01, finding
ui-dialogs-validation/design/26-status-map-and-no-problem-dial (часть 2/2:
диалог "что делать с оставшимися файлами").

``apply_changes`` и ``on_analysis_finished`` строили дословно одинаковый
QMessageBox с тремя кнопками ("Показать…"/"Автоматически…"/"Ничего не
делать") и одной и той же веткой действий
(``check_show_all.setChecked(True); start_analysis()`` /
``auto_process_good_files()``); различались только заголовок, текст и
подписи двух первых кнопок. Канонический источник —
``TranslationValidatorPage._offer_remaining_good_files_dialog``.

(a) Характеризационные тесты фиксируют поведение канонического метода
    (какая кнопка -> какое действие) в изоляции.
(b) Тесты-маршрутизаторы патчат ``_offer_remaining_good_files_dialog`` и
    проверяют, что ОБА метода реально вызывают его с исходными
    заголовком/текстом/подписями кнопок, вместо своей встроенной копии.
    До рефакторинга (собственный inline QMessageBox) эти тесты ПАДАЮТ,
    после — проходят.
"""

import os
import types
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gemini_translator.ui.dialogs import validation as validation_module
from gemini_translator.ui.dialogs.validation import TranslationValidatorPage


class _OfferDialogHarness:
    _offer_remaining_good_files_dialog = TranslationValidatorPage._offer_remaining_good_files_dialog

    def __init__(self):
        self.check_show_all = MagicMock()
        self.start_analysis = MagicMock()
        self.auto_process_good_files = MagicMock()


def _fake_message_box_class(clicked_button_name):
    """Строит поддельный класс QMessageBox: addButton возвращает разные
    сентинелы по тексту кнопки, clickedButton() возвращает сентинел
    ``clicked_button_name`` ('show'/'auto'/'cancel'/None)."""

    sentinels = {"show": object(), "auto": object(), "cancel": object()}

    class _FakeIcon:
        Question = object()

    class _FakeButtonRole:
        AcceptRole = object()
        ActionRole = object()
        RejectRole = object()

    class _FakeMsgBox:
        instances = []
        Icon = _FakeIcon
        ButtonRole = _FakeButtonRole

        def __init__(self, parent=None):
            self.parent = parent
            self.title = None
            self.text = None
            self.buttons = {}
            _FakeMsgBox.instances.append(self)

        def setWindowTitle(self, title):
            self.title = title

        def setText(self, text):
            self.text = text

        def setIcon(self, icon):
            pass

        def addButton(self, text, role):
            # Порядок вызовов в _offer_remaining_good_files_dialog фиксирован:
            # show, затем auto, затем cancel — используем его вместо
            # угадывания по тексту (подписи кнопок — свободный параметр).
            key = ("show", "auto", "cancel")[len(self.buttons)]
            sentinel = sentinels[key]
            self.buttons[key] = (text, sentinel)
            return sentinel

        def exec(self):
            pass

        def clickedButton(self):
            return sentinels.get(clicked_button_name)

    return _FakeMsgBox


class OfferRemainingGoodFilesDialogCharacterizationTests(unittest.TestCase):
    """(a) Характеризация канонической реализации."""

    def test_show_button_enables_show_all_and_starts_analysis(self):
        harness = _OfferDialogHarness()
        with patch.object(validation_module, "QMessageBox", _fake_message_box_class("show")):
            harness._offer_remaining_good_files_dialog(
                "Заголовок", "Текст", "Показать все", "Автоматически пометить",
            )

        harness.check_show_all.setChecked.assert_called_once_with(True)
        harness.start_analysis.assert_called_once()
        harness.auto_process_good_files.assert_not_called()

    def test_auto_button_marks_good_files_automatically(self):
        harness = _OfferDialogHarness()
        with patch.object(validation_module, "QMessageBox", _fake_message_box_class("auto")):
            harness._offer_remaining_good_files_dialog(
                "Заголовок", "Текст", "Показать все", "Автоматически пометить",
            )

        harness.auto_process_good_files.assert_called_once()
        harness.start_analysis.assert_not_called()

    def test_cancel_button_does_nothing(self):
        harness = _OfferDialogHarness()
        with patch.object(validation_module, "QMessageBox", _fake_message_box_class("cancel")):
            harness._offer_remaining_good_files_dialog(
                "Заголовок", "Текст", "Показать все", "Автоматически пометить",
            )

        harness.start_analysis.assert_not_called()
        harness.auto_process_good_files.assert_not_called()

    def test_title_text_and_button_labels_are_passed_through(self):
        harness = _OfferDialogHarness()
        fake_cls = _fake_message_box_class("cancel")
        with patch.object(validation_module, "QMessageBox", fake_cls):
            harness._offer_remaining_good_files_dialog(
                "Мой заголовок", "Мой текст", "Кнопка показать", "Кнопка авто",
            )

        box = fake_cls.instances[-1]
        self.assertEqual(box.title, "Мой заголовок")
        self.assertEqual(box.text, "Мой текст")
        self.assertEqual(box.buttons["show"][0], "Кнопка показать")
        self.assertEqual(box.buttons["auto"][0], "Кнопка авто")


class _RoutingHarnessBase:
    def __init__(self):
        self.project_manager = MagicMock()
        self.translated_folder = "/tmp/does-not-matter"
        self.results_data = {}
        self.table_results = MagicMock()
        self.table_results.rowCount.return_value = 0
        self.check_show_all = MagicMock()
        self.check_show_all.isChecked.return_value = False
        self._are_any_translated_files_left = MagicMock(return_value=True)
        self._sync_data_with_visual_order = MagicMock()
        self._recalc_untranslated_stats_ui = MagicMock()
        self._update_analyze_button_state = MagicMock()
        self._populate_initial_table = MagicMock()
        self._show_results_tab = MagicMock()
        self.dirty_files = set()
        self._offer_remaining_good_files_dialog = MagicMock()


class ApplyChangesRoutingTests(unittest.TestCase):
    def test_apply_changes_delegates_to_shared_dialog_helper(self):
        harness = _RoutingHarnessBase()
        harness.results_data = {
            0: {"internal_html_path": "Text/ch1.xhtml", "status": "delete", "path": "/tmp/does-not-exist.html"},
        }
        harness.apply_changes = types.MethodType(TranslationValidatorPage.apply_changes, harness)

        with patch.object(validation_module, "QMessageBox"):
            harness.apply_changes()

        harness._offer_remaining_good_files_dialog.assert_called_once_with(
            "Проблемные файлы обработаны",
            "Что делать с оставшимися 'хорошими' файлами?",
            "Показать для проверки",
            "Автоматически пометить 'Готовыми'",
        )


class OnAnalysisFinishedRoutingTests(unittest.TestCase):
    def test_on_analysis_finished_delegates_to_shared_dialog_helper(self):
        harness = _RoutingHarnessBase()
        harness.lbl_status = MagicMock()
        harness.btn_analyze = MagicMock()
        harness.btn_exceptions_manager = MagicMock()
        harness._write_validation_snapshot = MagicMock()
        harness.on_analysis_finished = types.MethodType(
            TranslationValidatorPage.on_analysis_finished, harness
        )

        harness.on_analysis_finished(5, 0)

        harness._offer_remaining_good_files_dialog.assert_called_once_with(
            "Проблем не найдено",
            "Первичная проверка не нашла проблемных файлов. Что вы хотите сделать?",
            "Показать все для ручной проверки",
            "Считать все 'Готовыми' и переместить",
        )


if __name__ == "__main__":
    unittest.main()
