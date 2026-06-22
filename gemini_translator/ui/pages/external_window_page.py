"""Shell page adapter for legacy QMainWindow-based tools."""
from __future__ import annotations

from PyQt6 import QtCore, QtWidgets

from gemini_translator.ui.shell import ShellPage


class EmbeddedWindowPage(ShellPage):
    """Hosts an existing window as a page in the shared navigation shell."""

    def __init__(self, embedded_window: QtWidgets.QWidget, title: str | None = None, parent=None):
        super().__init__(parent)
        self.embedded_window = embedded_window
        self._page_title = title or embedded_window.windowTitle()
        self._embedded_closed = False
        self._shell_back_closing = False

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._embed_window()
        layout.addWidget(self.embedded_window, 1)

        if hasattr(self.embedded_window, "set_return_to_menu_handler"):
            self.embedded_window.set_return_to_menu_handler(self._return_to_shell_menu)

    def get_page_title(self) -> str:
        return self._page_title

    def on_enter(self) -> None:
        self.embedded_window.show()

    def can_leave(self) -> bool:
        if self._embedded_closed:
            return True

        self._shell_back_closing = True
        try:
            accepted = bool(self.embedded_window.close())
        finally:
            self._shell_back_closing = False

        self._embedded_closed = accepted
        return accepted

    def on_leave(self) -> None:
        if not self._embedded_closed:
            self.embedded_window.close()
            self._embedded_closed = True
        self.embedded_window.deleteLater()

    def _embed_window(self) -> None:
        self.embedded_window.setParent(self)
        self.embedded_window.setWindowFlag(QtCore.Qt.WindowType.Window, False)
        self.embedded_window.setWindowFlag(QtCore.Qt.WindowType.Dialog, False)
        self.embedded_window.setWindowFlag(QtCore.Qt.WindowType.Widget, True)
        self.embedded_window.setWindowModality(QtCore.Qt.WindowModality.NonModal)
        self.embedded_window.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )

    def _return_to_shell_menu(self) -> None:
        self._embedded_closed = True
        if not self._shell_back_closing:
            self.request_back.emit()
