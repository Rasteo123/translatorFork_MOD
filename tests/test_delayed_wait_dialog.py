import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtTest, QtWidgets

from gemini_translator.ui.wait_dialogs import show_when_slow


class ShowWhenSlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _make_dialog(self):
        dialog = QtWidgets.QMessageBox()
        dialog.setText("Идет анализ проекта…")
        dialog.setStandardButtons(QtWidgets.QMessageBox.StandardButton.NoButton)
        self.addCleanup(dialog.deleteLater)
        return dialog

    def test_dialog_not_shown_immediately(self):
        dialog = self._make_dialog()
        show_when_slow(dialog, delay_ms=100)
        self.assertFalse(dialog.isVisible())

    def test_dialog_appears_when_operation_is_slow(self):
        dialog = self._make_dialog()
        show_when_slow(dialog, delay_ms=100)
        QtTest.QTest.qWait(250)
        self.assertTrue(dialog.isVisible())
        dialog.accept()

    def test_dialog_never_flashes_for_fast_operation(self):
        """Быстрая фоновая операция закрывает диалог до истечения задержки —
        маленькое окно не должно мигнуть вообще."""
        dialog = self._make_dialog()
        show_when_slow(dialog, delay_ms=100)
        dialog.accept()  # операция завершилась мгновенно
        QtTest.QTest.qWait(250)
        self.assertFalse(dialog.isVisible())


if __name__ == "__main__":
    unittest.main()
