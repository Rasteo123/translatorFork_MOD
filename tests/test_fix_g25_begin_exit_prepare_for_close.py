# -*- coding: utf-8 -*-
"""Регрессия для ui-pages-shell/bugs/1-update-exit-bypasses-unsaved-g.

HomePage._begin_exit() (автоперезапуск после установки обновления) обязан
опросить текущую страницу шелла через ``_prepare_for_close()`` ДО того, как
выставляет ``is_updating`` и закрывает окно — иначе MainShell.closeEvent
безусловно accept()-ит закрытие в обход вето живого воркера и диалога
несохранённых изменений (см. gemini_translator/ui/shell.py:279-282).
"""
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore, QtWidgets

from gemini_translator.ui.shell import MainShell, ShellPage
from gemini_translator.ui.pages.home_page import HomePage
from gemini_translator.utils import updater as upd


class _VetoPage(ShellPage):
    """Заглушка вместо InitialSetupPage с живым воркером/несохранёнными данными:
    ``_prepare_for_close`` отказывает в закрытии, как реальная страница."""

    def __init__(self):
        super().__init__()
        self.calls = 0

    def _prepare_for_close(self):
        self.calls += 1
        return False


class BeginExitPrepareForCloseTests(unittest.TestCase):
    def setUp(self):
        # Изоляция от заглушек менеджера настроек, которые другие тесты оставляют
        # на QApplication: HomePage.__init__ опрашивает proxy-настройки.
        patcher = patch.object(HomePage, "_settings_manager", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _shell_with_veto_page(self):
        shell = MainShell()
        # hide(), не close(): при вето close() уйдёт в обычный closeEvent
        # с модальным "Вы точно хотите выйти?" (is_updating не выставлен) —
        # в offscreen-тесте это повесит cleanup на nested event loop.
        self.addCleanup(shell.hide)
        home = HomePage()
        shell.set_home(home)
        veto_page = _VetoPage()
        shell.navigation.push(veto_page)
        shell.show()
        return shell, home, veto_page

    @patch("gemini_translator.utils.update_installer.log_update_event")
    def test_begin_exit_respects_prepare_for_close_veto(self, _mock_log):
        shell, home, veto_page = self._shell_with_veto_page()

        with patch.object(QtWidgets.QMessageBox, "information"):
            home._begin_exit()

        self.assertEqual(
            veto_page.calls, 1,
            "_begin_exit должен опросить _prepare_for_close текущей страницы")
        self.assertIsNot(
            shell.property("is_updating"), True,
            "при вето is_updating не должен выставляться — иначе closeEvent "
            "безусловно accept()-ит закрытие в обход вето")
        self.assertTrue(
            shell.isVisible(),
            "окно не должно закрываться, пока страница отказывает в закрытии")

    @patch("gemini_translator.utils.update_installer.log_update_event")
    def test_begin_exit_still_exits_when_page_allows_close(self, _mock_log):
        """Контроль: когда страница не возражает, обычный путь выхода работает."""
        shell = MainShell()
        self.addCleanup(shell.hide)
        home = HomePage()
        shell.set_home(home)
        shell.show()

        # _begin_exit заводит одноразовый аварийный QTimer с os._exit(0) на
        # случай зависшего штатного завершения (home_page.py). В боевом
        # прогоне процесс уже завершился бы раньше 15с, но если ЭТОТ тест
        # упадёт, pytest удержит кадры трейсбека (shell -> HomePage -> таймер)
        # до конца сессии — и весь набор будет молча убит os._exit(0) с кодом
        # 0 (см. ревью g25). Глушим аварийный выход и на этом пути.
        with patch.object(QtWidgets.QApplication, "instance", return_value=None), \
             patch("gemini_translator.ui.pages.home_page.os._exit") as emergency_exit:
            home._begin_exit()

        self.assertTrue(shell.property("is_updating"))
        self.assertFalse(shell.isVisible())
        self.assertEqual(home._update_state, upd.UpdateState.EXITING)
        for timer in home.findChildren(QtCore.QTimer):
            timer.stop()
        emergency_exit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
