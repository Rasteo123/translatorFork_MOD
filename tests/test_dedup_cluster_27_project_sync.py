# -*- coding: utf-8 -*-
"""
Дедуп: cluster-27 (_run_project_sync/_on_sync_finished скопированы между
setup.py, epub.py и validation.py).

К моменту этого рефакторинга обе копии в epub.py уже вызывали общий
модульный хелпер `run_project_migrator_sync` (тогда `_run_project_migrator_sync`),
корректно оборачивающий показ wait-диалога через show_when_slow — эта часть
кластера была закрыта предыдущей волной (см.
test_dedup_finding_ui_dialogs_epub_consistency_design_3_epub_run_project_sync_diverged.py).

Оставшийся дубль: `InitialSetupPage._run_project_sync` (setup.py) и
`TranslationValidatorPage._run_project_sync_and_reload` (validation.py)
у каждой были СВОИ копии того же построения wait_dialog/ProjectMigrator/
SyncThread (текстуально идентичного коду в epub.py), хотя уже вызывали
show_when_slow правильно — то есть поведенческого расхождения тут не было,
только дублирование кода.

Канонической реализацией назначен `run_project_migrator_sync` в epub.py
(единственный модуль из уже допустимого списка файлов, где эта функция уже
существовала и использовалась двумя местами) — setup.py и validation.py
теперь импортируют её вместо собственных копий.

Характеризационная часть: фиксирует поведение канонической функции —
отложенный показ через show_when_slow, порядок аргументов ProjectMigrator,
parent_widget у SyncThread, подключение finished_sync к on_finished,
запуск потока.

Тест-маршрутизация: подменяет `run_project_migrator_sync` в пространстве
имён setup.py / validation.py и проверяет, что вызов идёт через неё с
ожидаемыми аргументами (путь к папке, путь к epub, тексты диалога, callback
завершения). До рефакторинга оба места имели собственный инлайн-код и не
обращались к этому имени вовсе (AttributeError при патче или, если бы имя
существовало по случайности, нулевой call_count) — тест был бы RED.
"""
import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

import gemini_translator.ui.dialogs.epub as epub_module
# NB: не называть алиас "setup_dialog_module" — pytest трактует такое имя на уровне
# модуля теста как xunit-style хук и падает на инспекции module.__code__.
import gemini_translator.ui.dialogs.setup as setup_dialog_module
import gemini_translator.ui.dialogs.validation as validation_module
from gemini_translator.ui.dialogs.epub import run_project_migrator_sync
from gemini_translator.ui.dialogs.setup import InitialSetupPage
from gemini_translator.ui.dialogs.validation import TranslationValidatorPage


class _FakeProjectManager:
    pass


def _make_widget(cls, **attrs):
    widget = cls.__new__(cls)
    QtWidgets.QWidget.__init__(widget, None)
    for name, value in attrs.items():
        setattr(widget, name, value)
    return widget


class RunProjectMigratorSyncCharacterizationTests(unittest.TestCase):
    """(а) Характеризационные тесты канонической реализации."""

    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.widget = _make_widget(QtWidgets.QWidget)
        self.addCleanup(self.widget.deleteLater)

    def test_builds_modal_no_button_wait_dialog_with_given_title_and_text(self):
        # QMessageBox.windowTitle()/text() ненадёжны для чтения на всех
        # платформах офскрин-рендеринга (на некоторых стилях геттер
        # возвращает '' независимо от setWindowTitle), поэтому подменяем сам
        # класс и проверяем, какие setter'ы вызваны — а не что вернёт геттер.
        fake_dialog = mock.Mock()
        with mock.patch.object(epub_module, "QMessageBox", return_value=fake_dialog) as fake_qmessagebox_cls, \
             mock.patch.object(epub_module, "ProjectMigrator", return_value=mock.Mock()), \
             mock.patch.object(epub_module, "SyncThread") as fake_sync_thread_cls, \
             mock.patch.object(epub_module, "show_when_slow") as fake_show_when_slow:
            fake_sync_thread_cls.return_value = mock.Mock()

            run_project_migrator_sync(
                self.widget, _FakeProjectManager(), "/tmp/out", "/tmp/book.epub",
                "Заголовок", "Текст ожидания…", mock.Mock(),
            )

            fake_qmessagebox_cls.assert_called_once_with(self.widget)
            fake_dialog.setWindowTitle.assert_called_once_with("Заголовок")
            fake_dialog.setText.assert_called_once_with("Текст ожидания…")
            fake_dialog.setStandardButtons.assert_called_once_with(
                fake_qmessagebox_cls.StandardButton.NoButton
            )
            fake_dialog.setModal.assert_called_once_with(True)
            self.assertIs(self.widget.wait_dialog, fake_dialog)
            # Отложенный показ через show_when_slow — не синхронный .show().
            fake_dialog.show.assert_not_called()
            fake_show_when_slow.assert_called_once_with(fake_dialog)

    def test_constructs_migrator_with_folder_epub_path_and_manager_in_order(self):
        project_manager = _FakeProjectManager()
        with mock.patch.object(epub_module, "ProjectMigrator") as fake_migrator_cls, \
             mock.patch.object(epub_module, "SyncThread") as fake_sync_thread_cls, \
             mock.patch.object(epub_module, "show_when_slow"):
            fake_sync_thread_cls.return_value = mock.Mock()

            run_project_migrator_sync(
                self.widget, project_manager, "/tmp/out", "/tmp/book.epub",
                "T", "M", mock.Mock(),
            )

            fake_migrator_cls.assert_called_once_with("/tmp/out", "/tmp/book.epub", project_manager)

    def test_sync_thread_gets_widget_as_parent_and_starts(self):
        on_finished = mock.Mock()
        with mock.patch.object(epub_module, "ProjectMigrator", return_value=mock.Mock()), \
             mock.patch.object(epub_module, "SyncThread") as fake_sync_thread_cls, \
             mock.patch.object(epub_module, "show_when_slow"):
            fake_thread = mock.Mock()
            fake_sync_thread_cls.return_value = fake_thread

            run_project_migrator_sync(
                self.widget, _FakeProjectManager(), "/tmp/out", "/tmp/book.epub",
                "T", "M", on_finished,
            )

            _, kwargs = fake_sync_thread_cls.call_args
            self.assertIs(kwargs.get("parent_widget"), self.widget)
            fake_thread.finished_sync.connect.assert_called_once_with(on_finished)
            fake_thread.start.assert_called_once()
            self.assertIs(self.widget.sync_thread, fake_thread)


class SetupPageRoutesThroughCanonicalSyncTests(unittest.TestCase):
    """(б) Маршрутизация: InitialSetupPage._run_project_sync -> run_project_migrator_sync."""

    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_run_project_sync_delegates_to_canonical_helper(self):
        page = _make_widget(
            InitialSetupPage,
            project_manager=_FakeProjectManager(),
            output_folder="/tmp/out",
            selected_file="/tmp/book.epub",
        )
        self.addCleanup(page.deleteLater)

        with mock.patch.object(setup_dialog_module, "run_project_migrator_sync") as fake_run:
            page._run_project_sync()

            fake_run.assert_called_once_with(
                page, page.project_manager, "/tmp/out", "/tmp/book.epub",
                "Синхронизация", "Идет анализ проекта…", page._on_sync_finished,
            )

    def test_run_project_sync_guard_skips_without_project_manager(self):
        page = _make_widget(
            InitialSetupPage,
            project_manager=None,
            output_folder="/tmp/out",
            selected_file="/tmp/book.epub",
        )
        self.addCleanup(page.deleteLater)

        with mock.patch.object(setup_dialog_module, "run_project_migrator_sync") as fake_run:
            page._run_project_sync()
            fake_run.assert_not_called()

    def test_setup_dialog_module_shares_the_epub_canonical_function_object(self):
        self.assertIs(setup_dialog_module.run_project_migrator_sync, epub_module.run_project_migrator_sync)


class ValidationPageRoutesThroughCanonicalSyncTests(unittest.TestCase):
    """(б) Маршрутизация: TranslationValidatorPage._run_project_sync_and_reload -> run_project_migrator_sync."""

    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_run_project_sync_and_reload_delegates_to_canonical_helper(self):
        page = _make_widget(
            TranslationValidatorPage,
            project_manager=_FakeProjectManager(),
            translated_folder="/tmp/translated",
            original_epub_path="/tmp/book.epub",
        )
        self.addCleanup(page.deleteLater)

        with mock.patch.object(validation_module, "run_project_migrator_sync") as fake_run:
            page._run_project_sync_and_reload()

            fake_run.assert_called_once_with(
                page, page.project_manager, "/tmp/translated", "/tmp/book.epub",
                "Синхронизация", "Идет анализ проекта…\nПожалуйста, подождите.",
                page._on_validator_sync_finished,
            )

    def test_run_project_sync_and_reload_guard_skips_without_project_manager(self):
        page = _make_widget(
            TranslationValidatorPage,
            project_manager=None,
            translated_folder="/tmp/translated",
            original_epub_path="/tmp/book.epub",
        )
        self.addCleanup(page.deleteLater)

        with mock.patch.object(validation_module, "run_project_migrator_sync") as fake_run:
            page._run_project_sync_and_reload()
            fake_run.assert_not_called()

    def test_validation_module_shares_the_epub_canonical_function_object(self):
        self.assertIs(validation_module.run_project_migrator_sync, epub_module.run_project_migrator_sync)


if __name__ == "__main__":
    unittest.main()
