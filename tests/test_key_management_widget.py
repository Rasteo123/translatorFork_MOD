import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from gemini_translator.ui.widgets.key_management_widget import AdaptiveControlsWidget


class _CountingButton(QtWidgets.QPushButton):
    def __init__(self, text=""):
        super().__init__(text)
        self.style_calls = 0

    def setStyleSheet(self, style):
        self.style_calls += 1
        super().setStyleSheet(style)


class AdaptiveControlsWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_resize_with_same_style_bucket_does_not_reapply_button_styles(self):
        arrow_buttons = [_CountingButton(text) for text in (">", "<", ">>", "<<")]
        action_buttons = [_CountingButton(text) for text in ("Add", "Reset", "Force", "Delete")]
        separator = QtWidgets.QFrame()
        separator.setFrameShape(QtWidgets.QFrame.Shape.HLine)

        widget = AdaptiveControlsWidget(arrow_buttons, action_buttons, separator)
        host = QtWidgets.QWidget()
        self.addCleanup(host.close)
        layout = QtWidgets.QVBoxLayout(host)
        layout.addWidget(widget)

        host.resize(320, 300)
        host.show()
        self.app.processEvents()

        initial_calls = sum(button.style_calls for button in arrow_buttons + action_buttons)
        self.assertGreater(initial_calls, 0)

        host.resize(320, 305)
        self.app.processEvents()

        repeated_calls = sum(button.style_calls for button in arrow_buttons + action_buttons)
        self.assertEqual(repeated_calls, initial_calls)

        host.resize(320, 420)
        self.app.processEvents()

        changed_calls = sum(button.style_calls for button in arrow_buttons + action_buttons)
        self.assertGreater(changed_calls, repeated_calls)


if __name__ == "__main__":
    unittest.main()
