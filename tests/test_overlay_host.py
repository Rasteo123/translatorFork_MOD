import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore, QtWidgets
from PyQt6.QtWidgets import QDialog

from gemini_translator.ui.overlay_host import (
    OverlayHost,
    find_overlay_host,
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


if __name__ == "__main__":
    unittest.main()
