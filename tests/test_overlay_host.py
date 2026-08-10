import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore, QtWidgets
from PyQt6.QtWidgets import QDialog

from gemini_translator.ui.overlay_host import (
    OverlayHost,
    exec_dialog,
    find_overlay_host,
    install_message_box_overlay,
    present_dialog,
)
from gemini_translator.ui.shell import MainShell, ShellPage


class SimpleDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        self.edit = QtWidgets.QLineEdit()
        layout.addWidget(self.edit)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


def _drain(app):
    for _ in range(5):
        app.processEvents()


class OverlayHostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _shell(self):
        shell = MainShell()
        shell.resize_controller.set_duration(0)
        self.addCleanup(shell.close)
        self.addCleanup(shell.hide)
        page = ShellPage()
        shell.set_home(page)
        shell.show()
        _drain(self.app)
        return shell

    def test_shell_exposes_overlay_host(self):
        shell = self._shell()
        self.assertIsInstance(shell.overlay_host, OverlayHost)
        self.assertIs(find_overlay_host(shell.navigation.current_page()), shell.overlay_host)

    def test_present_shows_dialog_and_blocks_background(self):
        shell = self._shell()
        dialog = SimpleDialog()
        shell.overlay_host.present(dialog)
        _drain(self.app)
        self.assertTrue(shell.overlay_host.isVisible())
        self.assertTrue(dialog.isVisible())
        # Фон (навигация и страницы) отключён на время показа карточки.
        self.assertFalse(shell.centralWidget().isEnabled())
        # Диалог встроен: он не отдельное окно.
        self.assertFalse(dialog.isWindow())

    def test_accept_delivers_result_and_restores_background(self):
        shell = self._shell()
        dialog = SimpleDialog()
        results = []
        shell.overlay_host.present(dialog, results.append)
        _drain(self.app)
        dialog.accept()
        _drain(self.app)
        self.assertEqual(results, [QDialog.DialogCode.Accepted])
        self.assertFalse(shell.overlay_host.isVisible())
        self.assertTrue(shell.centralWidget().isEnabled())

    def test_escape_rejects_top_dialog(self):
        from PyQt6 import QtTest

        shell = self._shell()
        dialog = SimpleDialog()
        results = []
        shell.overlay_host.present(dialog, results.append)
        _drain(self.app)
        QtTest.QTest.keyClick(dialog, QtCore.Qt.Key.Key_Escape)
        _drain(self.app)
        self.assertEqual(results, [QDialog.DialogCode.Rejected])

    def test_nested_dialogs_stack_and_unwind(self):
        shell = self._shell()
        first = SimpleDialog()
        second = SimpleDialog()
        order = []
        shell.overlay_host.present(first, lambda r: order.append(("first", r)))
        _drain(self.app)
        shell.overlay_host.present(second, lambda r: order.append(("second", r)))
        _drain(self.app)
        # Пока открыта верхняя карточка, нижняя заблокирована.
        self.assertFalse(first.isEnabled())
        second.reject()
        _drain(self.app)
        self.assertTrue(first.isEnabled())
        self.assertTrue(shell.overlay_host.isVisible())
        first.accept()
        _drain(self.app)
        self.assertEqual(
            order,
            [
                ("second", QDialog.DialogCode.Rejected),
                ("first", QDialog.DialogCode.Accepted),
            ],
        )
        self.assertFalse(shell.overlay_host.isVisible())

    def test_card_clamped_to_host_size(self):
        shell = self._shell()
        dialog = SimpleDialog()
        dialog.resize(5000, 4000)
        shell.overlay_host.present(dialog)
        _drain(self.app)
        card = dialog.parentWidget()
        self.assertLessEqual(card.width(), shell.overlay_host.width())
        self.assertLessEqual(card.height(), shell.overlay_host.height())

    def test_present_dialog_falls_back_to_window_modal(self):
        parent = QtWidgets.QWidget()
        self.addCleanup(parent.close)
        parent.show()
        _drain(self.app)
        dialog = SimpleDialog(parent)
        results = []
        present_dialog(parent, dialog, results.append)
        _drain(self.app)
        self.assertTrue(dialog.isVisible())
        self.assertTrue(dialog.isWindow())
        dialog.reject()
        _drain(self.app)
        self.assertEqual(results, [QDialog.DialogCode.Rejected])

    def test_present_dialog_uses_overlay_when_hosted(self):
        shell = self._shell()
        page = shell.navigation.current_page()
        dialog = SimpleDialog(page)
        present_dialog(page, dialog)
        _drain(self.app)
        self.assertTrue(shell.overlay_host.isVisible())
        self.assertFalse(dialog.isWindow())


class ExecDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _shell(self):
        shell = MainShell()
        shell.resize_controller.set_duration(0)
        self.addCleanup(shell.close)
        self.addCleanup(shell.hide)
        shell.set_home(ShellPage())
        shell.show()
        for _ in range(5):
            self.app.processEvents()
        return shell

    def test_exec_dialog_blocks_until_accept_and_returns_result(self):
        shell = self._shell()
        page = shell.navigation.current_page()
        dialog = SimpleDialog(page)
        # Принимаем диалог из отложенного таймера, пока exec_dialog крутит
        # вложенный цикл.
        QtCore.QTimer.singleShot(0, dialog.accept)
        result = exec_dialog(page, dialog)
        self.assertEqual(result, int(QDialog.DialogCode.Accepted))
        self.assertFalse(shell.overlay_host.isVisible())
        # Виджеты диалога ещё живы сразу после возврата (deleteLater
        # обрабатывается позже) — вызывающий код может читать значения.
        self.assertEqual(dialog.edit.text(), "")

    def test_exec_dialog_falls_back_to_native_exec_without_host(self):
        parent = QtWidgets.QWidget()
        self.addCleanup(parent.close)
        dialog = SimpleDialog(parent)
        called = []
        dialog.exec = lambda: called.append(True) or int(QDialog.DialogCode.Rejected)
        result = exec_dialog(parent, dialog)
        self.assertEqual(result, int(QDialog.DialogCode.Rejected))
        self.assertEqual(called, [True])


class MessageBoxOverlayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        install_message_box_overlay()

    def _shell(self):
        shell = MainShell()
        shell.resize_controller.set_duration(0)
        self.addCleanup(shell.close)
        self.addCleanup(shell.hide)
        shell.set_home(ShellPage())
        shell.show()
        for _ in range(5):
            self.app.processEvents()
        return shell

    def test_information_shows_as_overlay_card(self):
        shell = self._shell()
        page = shell.navigation.current_page()
        seen = {}

        def _capture():
            boxes = [
                w
                for w in QtWidgets.QApplication.allWidgets()
                if isinstance(w, QtWidgets.QMessageBox) and w.isVisible()
            ]
            seen["boxes"] = boxes
            seen["overlay_visible"] = shell.overlay_host.isVisible()
            for box in boxes:
                seen["is_window"] = box.isWindow()
                box.button(QtWidgets.QMessageBox.StandardButton.Ok).click()

        QtCore.QTimer.singleShot(0, _capture)
        result = QtWidgets.QMessageBox.information(page, "Заголовок", "Текст")
        self.assertTrue(seen["overlay_visible"])
        self.assertFalse(seen["is_window"])
        self.assertEqual(result, QtWidgets.QMessageBox.StandardButton.Ok)
        self.assertFalse(shell.overlay_host.isVisible())

    def test_question_returns_chosen_button(self):
        shell = self._shell()
        page = shell.navigation.current_page()

        def _click_no():
            for w in QtWidgets.QApplication.allWidgets():
                if isinstance(w, QtWidgets.QMessageBox) and w.isVisible():
                    no_btn = w.button(QtWidgets.QMessageBox.StandardButton.No)
                    no_btn.click()

        QtCore.QTimer.singleShot(0, _click_no)
        result = QtWidgets.QMessageBox.question(page, "Вопрос", "Да или нет?")
        self.assertEqual(result, QtWidgets.QMessageBox.StandardButton.No)

    def test_instance_exec_goes_through_overlay(self):
        shell = self._shell()
        page = shell.navigation.current_page()
        box = QtWidgets.QMessageBox(page)
        box.setText("Сообщение")
        box.setStandardButtons(QtWidgets.QMessageBox.StandardButton.Ok)
        seen = {}

        def _capture():
            seen["overlay_visible"] = shell.overlay_host.isVisible()
            seen["is_window"] = box.isWindow()
            box.accept()

        QtCore.QTimer.singleShot(0, _capture)
        box.exec()
        self.assertTrue(seen["overlay_visible"])
        self.assertFalse(seen["is_window"])

    def test_static_without_parent_uses_native_path(self):
        # Без родителя в шелле нет хоста — должен работать нативный путь.
        def _close_any():
            for w in QtWidgets.QApplication.allWidgets():
                if isinstance(w, QtWidgets.QMessageBox) and w.isVisible():
                    w.button(QtWidgets.QMessageBox.StandardButton.Ok).click()

        QtCore.QTimer.singleShot(0, _close_any)
        result = QtWidgets.QMessageBox.information(None, "Т", "Т")
        self.assertEqual(result, QtWidgets.QMessageBox.StandardButton.Ok)


if __name__ == "__main__":
    unittest.main()
