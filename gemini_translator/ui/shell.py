"""Persistent navigation shell for the translator-side tools.

Hosts pages (``ShellPage``) in a stack and provides Back/title navigation,
replacing the previous one-window-per-module model.
"""
from __future__ import annotations

from PyQt6 import QtCore, QtWidgets


class ShellPage(QtWidgets.QWidget):
    """Base class for any full-window page hosted by :class:`MainShell`.

    Subclasses set ``page_title`` and may override the lifecycle hooks.
    Pages ask the shell to navigate by emitting ``request_back`` /
    ``request_push`` instead of opening their own windows.
    """

    #: Emitted when the page wants the shell to go back one level.
    request_back = QtCore.pyqtSignal()
    #: Emitted with a ``ShellPage`` instance the page wants pushed on top.
    #: Typed ``object`` because PyQt6 cannot declare a signal of a
    #: forward-referenced class; callers must pass ``ShellPage`` instances.
    request_push = QtCore.pyqtSignal(object)

    #: Title shown in the shell nav bar while this page is current.
    page_title: str = ""

    def get_page_title(self) -> str:
        return self.page_title

    def on_enter(self) -> None:
        """Called when the page becomes the current (visible) page."""

    def on_leave(self) -> None:
        """Called when the page stops being current (pushed-over or popped)."""

    def can_leave(self) -> bool:
        """Return ``False`` to veto leaving (e.g. unsaved/running work)."""
        return True


class NavigationController(QtCore.QObject):
    """Owns the page stack and drives a ``QStackedWidget``.

    The home page (index 0) can never be popped. Pushing a page wires its
    ``request_back``/``request_push`` signals to this controller.
    """

    stack_changed = QtCore.pyqtSignal()

    def __init__(self, stack: QtWidgets.QStackedWidget, parent=None):
        super().__init__(parent)
        self._stack = stack
        self._pages: list[ShellPage] = []

    @property
    def depth(self) -> int:
        return len(self._pages)

    def current_page(self) -> "ShellPage | None":
        return self._pages[-1] if self._pages else None

    def set_home(self, page: ShellPage) -> None:
        if self._pages:
            raise RuntimeError("Home page already set")
        # Home has no back navigation; request_back is intentionally not wired.
        page.request_push.connect(self.push)
        self._pages.append(page)
        self._stack.addWidget(page)
        self._stack.setCurrentWidget(page)
        page.on_enter()
        self.stack_changed.emit()

    def push(self, page: ShellPage) -> None:
        if not self._pages:
            raise RuntimeError("set_home must be called before push")
        page.request_back.connect(self.pop)
        page.request_push.connect(self.push)
        # on_leave fires only in pop() — i.e. when a page is actually removed.
        # Merely covering a page does NOT call on_leave.
        self._pages.append(page)
        self._stack.addWidget(page)
        self._stack.setCurrentWidget(page)
        page.on_enter()
        self.stack_changed.emit()

    def pop(self) -> bool:
        if len(self._pages) <= 1:
            return False
        page = self._pages[-1]
        if not page.can_leave():
            return False
        page.on_leave()
        self._pages.pop()
        self._stack.removeWidget(page)
        page.request_back.disconnect(self.pop)
        page.request_push.disconnect(self.push)
        page.deleteLater()
        previous = self._pages[-1]
        self._stack.setCurrentWidget(previous)
        previous.on_enter()
        self.stack_changed.emit()
        return True

    def reset_to_home(self) -> None:
        """Pop pages until only the home page remains.

        Stops at the first page whose ``can_leave()`` vetoes leaving.
        """
        while self.pop():
            pass


class MainShell(QtWidgets.QMainWindow):
    """The single persistent window for the translator-side tools.

    A ``QMainWindow`` (not a dialog), so macOS gives it a native minimize
    button. Shows a Back button + title bar above the page stack.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Gemini EPUB Translator")
        self.resize(1100, 800)
        # Always set translucent background to support macOS vibrancy correctly.
        # When vibrancy is off, the solid background is drawn by Qt over the translucent layer.
        # self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        # self.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)

        central = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._nav_bar = QtWidgets.QWidget()
        self._nav_bar.setObjectName("navBar")
        nav_layout = QtWidgets.QHBoxLayout(self._nav_bar)
        nav_layout.setContentsMargins(8, 6, 8, 6)
        nav_layout.setSpacing(8)
        self._back_button = QtWidgets.QPushButton("← Назад")
        self._back_button.clicked.connect(self._on_back_clicked)
        self._title_label = QtWidgets.QLabel("")
        # Title sits on its own "tile" so it reads as a deliberate chip in every
        # window instead of bare text floating on the gray window background.
        self._title_label.setObjectName("navTitleChip")
        nav_layout.addWidget(self._back_button)
        nav_layout.addWidget(self._title_label)
        nav_layout.addStretch(1)

        self._stack = QtWidgets.QStackedWidget()

        root.addWidget(self._nav_bar)
        root.addWidget(self._stack, 1)
        self.setCentralWidget(central)

        self.navigation = NavigationController(self._stack, self)
        self.navigation.stack_changed.connect(self._sync_nav_bar)
        self._sync_nav_bar()

    def set_home(self, page: ShellPage) -> None:
        self.navigation.set_home(page)

    def _on_back_clicked(self) -> None:
        self.navigation.pop()

    def _sync_nav_bar(self) -> None:
        page = self.navigation.current_page()
        title = page.get_page_title() if page else ""
        self._title_label.setText(title)
        # Hide the chip entirely when there is no title (e.g. home) so an empty
        # tile never shows.
        self._title_label.setVisible(bool(title))
        self._back_button.setVisible(self.navigation.depth > 1)

    def closeEvent(self, event) -> None:
        if not self.isVisible():
            event.accept()
            return

        msg_box = QtWidgets.QMessageBox(self)
        msg_box.setWindowTitle("Выход")
        msg_box.setText("Вы действительно хотите выйти из программы?")
        msg_box.setIcon(QtWidgets.QMessageBox.Icon.Question)

        btn_exit = msg_box.addButton("Выйти", QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        btn_cancel = msg_box.addButton("Отмена", QtWidgets.QMessageBox.ButtonRole.RejectRole)

        msg_box.exec()
        if msg_box.clickedButton() == btn_cancel:
            event.ignore()
            return

        page = self.navigation.current_page()
        if hasattr(page, "_prepare_for_close"):
            if not page._prepare_for_close():
                event.ignore()
                return

        event.accept()
