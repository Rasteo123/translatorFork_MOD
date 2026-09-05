# -*- coding: utf-8 -*-
"""Регрессии по замечаниям ревью g25 к предыдущему фиксу
ui-pages-shell/bugs/1-update-exit-bypasses-unsaved-g.

1) Ветка вето в HomePage._begin_exit() обязана вернуть апдейтер в состояние
   IDLE и сообщить пользователю, а не только написать строку в лог — иначе
   кнопка "Проверить обновления" замирает в состоянии PREPARING/EXITING до
   конца сеанса, а check_for_updates() молча выходит на guard'е "state is not
   IDLE" (см. home_page.py:316).

2) На git-обновлении вето страницы должно быть опрошено ДО
   QProcess.startDetached(sys.executable, sys.argv) — иначе при вето
   запускается вторая копия приложения поверх ещё не закрытой первой (обе
   работают с одними и теми же настройками/БД очереди).

3) На release/archive-обновлении вето страницы должно быть опрошено ДО
   запуска detached-хелпера установки (self._run_prepare_worker(job)) —
   иначе хелпер уже ждёт завершения нашего PID (APP_EXIT_WAIT_S = 120s) пока
   пользователь отвечает на модальный диалог "Сохранить/Выйти/Отмена", и при
   долгом ответе хелпер аварийно уходит "app still running; aborting
   untouched" без возможности повторить попытку.
"""
import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore, QtWidgets

from gemini_translator.ui.shell import MainShell, ShellPage
from gemini_translator.ui.pages.home_page import HomePage
from gemini_translator.utils import updater as upd


class _VetoPage(ShellPage):
    """Заглушка вместо InitialSetupPage: _prepare_for_close всегда отказывает."""

    def __init__(self):
        super().__init__()
        self.calls = 0

    def _prepare_for_close(self):
        self.calls += 1
        return False


class BeginExitVetoRestoresStateTests(unittest.TestCase):
    def setUp(self):
        # Изоляция от заглушек менеджера настроек, которые другие тесты оставляют
        # на QApplication: HomePage.__init__ опрашивает proxy-настройки.
        patcher = patch.object(HomePage, "_settings_manager", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _shell_with_veto_page(self, home):
        shell = MainShell()
        # hide(), не close(): при вето close() уйдёт в обычный closeEvent
        # с модальным "Вы точно хотите выйти?" (is_updating не выставлен).
        self.addCleanup(shell.hide)
        shell.set_home(home)
        veto_page = _VetoPage()
        shell.navigation.push(veto_page)
        shell.show()
        return shell, veto_page

    @patch("gemini_translator.utils.update_installer.log_update_event")
    def test_begin_exit_veto_resets_state_and_notifies(self, _mock_log):
        home = HomePage()
        home._set_update_state(upd.UpdateState.PREPARING)
        shell, veto_page = self._shell_with_veto_page(home)

        informed = MagicMock()
        with patch.object(QtWidgets.QMessageBox, "information", informed):
            home._begin_exit()

        self.assertEqual(veto_page.calls, 1)
        self.assertIs(
            home._update_state, upd.UpdateState.IDLE,
            "после вето апдейтер обязан вернуться в IDLE, иначе кнопка "
            "'Проверить обновления' замирает навсегда")
        self.assertTrue(
            home.btn_check_update.isEnabled(),
            "кнопка проверки обновлений должна снова стать доступной")
        informed.assert_called_once()


class GitUpdateVetoTests(unittest.TestCase):
    def setUp(self):
        # Изоляция от заглушек менеджера настроек, которые другие тесты оставляют
        # на QApplication: HomePage.__init__ опрашивает proxy-настройки.
        patcher = patch.object(HomePage, "_settings_manager", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    """Вето страницы на git-обновлении не должно порождать вторую копию."""

    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_git_update_checks_page_before_restart(self):
        home = HomePage()
        shell = MainShell()
        self.addCleanup(shell.hide)
        shell.set_home(home)
        veto_page = _VetoPage()
        shell.navigation.push(veto_page)
        shell.show()

        # Подменяем воркер, чтобы не дёргать реальный git и сразу же
        # синхронно вызвать сигнал done — нас интересует только обработчик
        # on_done, подключённый в _start_git_update.
        class _FakeWorker(QtCore.QObject):
            done = QtCore.pyqtSignal(object)
            failed = QtCore.pyqtSignal(str)

            def __init__(self, job, parent=None):
                super().__init__(parent)
                self._job = job

            def start(self):
                self.done.emit(self._job())

        info = MagicMock()
        with patch.object(upd, "FunctionWorker", _FakeWorker), \
             patch("gemini_translator.utils.update_installer.install_git_update",
                   return_value=None), \
             patch("PyQt6.QtCore.QProcess.startDetached") as started, \
             patch.object(HomePage, "_begin_exit") as begin_exit, \
             patch.object(QtWidgets.QMessageBox, "information"):
            home._start_git_update(info)

        self.assertEqual(veto_page.calls, 1)
        started.assert_not_called()
        begin_exit.assert_not_called()
        self.assertIs(
            home._update_state, upd.UpdateState.IDLE,
            "при вето апдейтер должен вернуться в IDLE, а не остаться "
            "в PREPARING")


class ReleaseInstallVetoTests(unittest.TestCase):
    def setUp(self):
        # Изоляция от заглушек менеджера настроек, которые другие тесты оставляют
        # на QApplication: HomePage.__init__ опрашивает proxy-настройки.
        patcher = patch.object(HomePage, "_settings_manager", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    """Вето страницы на release-пути должно предотвращать запуск установки."""

    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_prepare_release_install_checks_page_before_worker(self):
        home = HomePage()
        shell = MainShell()
        self.addCleanup(shell.hide)
        shell.set_home(home)
        veto_page = _VetoPage()
        shell.navigation.push(veto_page)
        shell.show()

        info = MagicMock(title_version="10.5.23")
        with patch.object(HomePage, "_run_prepare_worker") as run_worker, \
             patch.object(QtWidgets.QMessageBox, "information"):
            home._prepare_release_install(info, "/tmp/staged.zip")

        self.assertEqual(veto_page.calls, 1)
        run_worker.assert_not_called()
        self.assertIs(home._update_state, upd.UpdateState.IDLE)


if __name__ == "__main__":
    unittest.main()
