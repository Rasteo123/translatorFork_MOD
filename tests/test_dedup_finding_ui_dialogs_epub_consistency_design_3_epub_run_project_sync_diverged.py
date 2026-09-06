# -*- coding: utf-8 -*-
"""
Дедуп: finding-ui-dialogs-epub-consistency_design_3-epub-run-project-sync-diverged.

EpubHtmlSelectorDialog._run_project_sync и
TranslatedChaptersManagerDialog._run_project_sync_and_reload были скопированы
из InitialSetupPage._run_project_sync (setup.py), но потеряли отложенный показ
wait-диалога через show_when_slow — обе копии вызывали self.wait_dialog.show()
напрямую, из-за чего при быстрой синхронизации пользователь видел мигающий
модальный диалог вместо отложенного показа (как в setup.py:3579 и в самом
epub.py:845, _run_full_analysis).

Характеризационная часть: canонический show_when_slow не показывает диалог
синхронно в момент вызова (проверяется его собственным тестом в
test_delayed_wait_dialog.py) — здесь мы фиксируем ту же гарантию именно для
двух бывших копий.

Тест-маршрутизация: до рефакторинга оба метода вызывают
`self.wait_dialog.show()` напрямую, минуя show_when_slow, поэтому подмена
show_when_slow в модуле epub.py не перехватывает вызов — тест RED.
После рефакторинга оба метода идут через show_when_slow — тест GREEN.
"""
import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

import gemini_translator.ui.dialogs.epub as epub_module
from gemini_translator.ui.dialogs.epub import (
    EpubHtmlSelectorDialog,
    TranslatedChaptersManagerDialog,
)


class _FakeProjectManager:
    """Минимальная заглушка — реальный ProjectMigrator/SyncThread подменяются."""
    pass


class RunProjectSyncRoutesThroughShowWhenSlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _make_dialog(self, cls, **attrs):
        # Обходим тяжёлую бизнес-логику __init__, но конструируем нижележащий
        # C++/Qt-объект напрямую через QDialog.__init__, иначе QMessageBox(self)
        # падает с "super-class __init__() ... was never called".
        dialog = cls.__new__(cls)
        QtWidgets.QDialog.__init__(dialog, None)
        for name, value in attrs.items():
            setattr(dialog, name, value)
        self.addCleanup(dialog.deleteLater)
        return dialog

    def test_run_project_sync_uses_show_when_slow(self):
        dialog = self._make_dialog(
            EpubHtmlSelectorDialog,
            project_manager=_FakeProjectManager(),
            output_folder="/tmp/out",
            real_epub_path="/tmp/book.epub",
        )

        calls = []

        def fake_show_when_slow(wait_dialog, *args, **kwargs):
            calls.append(wait_dialog)
            return mock.Mock()

        # _run_project_sync (до рефакторинга) делает локальный
        # `from ...utils.project_migrator import ProjectMigrator, SyncThread`,
        # который перекрывает патч на epub_module — поэтому патчим ещё и
        # исходный модуль, чтобы не поднимался настоящий QThread на фейковом
        # project_manager.
        with mock.patch.object(epub_module, "ProjectMigrator", return_value=mock.Mock()), \
             mock.patch.object(epub_module, "SyncThread") as fake_sync_thread_cls, \
             mock.patch("gemini_translator.utils.project_migrator.ProjectMigrator", return_value=mock.Mock()), \
             mock.patch("gemini_translator.utils.project_migrator.SyncThread") as fake_sync_thread_cls_origin, \
             mock.patch.object(epub_module, "show_when_slow", side_effect=fake_show_when_slow) as fake_show_when_slow_fn:
            fake_thread_instance = mock.Mock()
            fake_sync_thread_cls.return_value = fake_thread_instance
            fake_sync_thread_cls_origin.return_value = fake_thread_instance

            dialog._run_project_sync()

            self.assertEqual(
                fake_show_when_slow_fn.call_count, 1,
                "_run_project_sync должен показывать wait_dialog через show_when_slow",
            )
            self.assertEqual(calls[0], dialog.wait_dialog)
            self.assertFalse(
                dialog.wait_dialog.isVisible(),
                "wait_dialog не должен показываться синхронно — show_when_slow сам решает, когда показать",
            )
            fake_thread_instance.start.assert_called_once()

    def test_run_project_sync_and_reload_uses_show_when_slow(self):
        dialog = self._make_dialog(
            TranslatedChaptersManagerDialog,
            project_manager=_FakeProjectManager(),
            translated_folder="/tmp/translated",
            original_epub_path="/tmp/book.epub",
        )

        calls = []

        def fake_show_when_slow(wait_dialog, *args, **kwargs):
            calls.append(wait_dialog)
            return mock.Mock()

        with mock.patch.object(epub_module, "ProjectMigrator", return_value=mock.Mock()), \
             mock.patch.object(epub_module, "SyncThread") as fake_sync_thread_cls, \
             mock.patch("gemini_translator.utils.project_migrator.ProjectMigrator", return_value=mock.Mock()), \
             mock.patch("gemini_translator.utils.project_migrator.SyncThread") as fake_sync_thread_cls_origin, \
             mock.patch.object(epub_module, "show_when_slow", side_effect=fake_show_when_slow) as fake_show_when_slow_fn:
            fake_thread_instance = mock.Mock()
            fake_sync_thread_cls.return_value = fake_thread_instance
            fake_sync_thread_cls_origin.return_value = fake_thread_instance

            dialog._run_project_sync_and_reload()

            self.assertEqual(
                fake_show_when_slow_fn.call_count, 1,
                "_run_project_sync_and_reload должен показывать wait_dialog через show_when_slow",
            )
            self.assertEqual(calls[0], dialog.wait_dialog)
            self.assertFalse(
                dialog.wait_dialog.isVisible(),
                "wait_dialog не должен показываться синхронно — show_when_slow сам решает, когда показать",
            )
            fake_thread_instance.start.assert_called_once()

    def test_run_project_sync_and_reload_still_guards_missing_epub_path(self):
        """Поведение-развилка (behavior_choice): guard TranslatedChaptersManagerDialog
        (project_manager/original_epub_path обязателен) сохраняется — это не то,
        что различало копии, но проверяем, что рефакторинг его не стёр."""
        dialog = self._make_dialog(
            TranslatedChaptersManagerDialog,
            project_manager=_FakeProjectManager(),
            translated_folder="/tmp/translated",
            original_epub_path=None,
        )

        with mock.patch.object(epub_module.QMessageBox, "warning") as fake_warning, \
             mock.patch.object(epub_module, "ProjectMigrator") as fake_migrator_cls, \
             mock.patch.object(epub_module, "SyncThread") as fake_sync_thread_cls, \
             mock.patch.object(epub_module, "show_when_slow") as fake_show_when_slow_fn:
            dialog._run_project_sync_and_reload()

            fake_warning.assert_called_once()
            fake_migrator_cls.assert_not_called()
            fake_sync_thread_cls.assert_not_called()
            fake_show_when_slow_fn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
