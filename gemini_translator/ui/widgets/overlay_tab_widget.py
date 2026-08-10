from PyQt6 import QtCore, QtWidgets
from PyQt6.QtWidgets import QWidget, QGridLayout, QStackedWidget, QTabBar, QScrollArea

#: Проявление содержимого вкладки при переключении.
TAB_FADE_MS = 120


def _fade_in_tab(widget: QWidget, duration_ms: int) -> None:
    """Короткий фейд контента вкладки с безусловной очисткой эффекта."""
    effect = QtWidgets.QGraphicsOpacityEffect(widget)
    effect.setOpacity(0.0)
    widget.setGraphicsEffect(effect)
    widget._tab_fade_effect = effect

    def _cleanup() -> None:
        try:
            if getattr(widget, "_tab_fade_effect", None) is effect:
                widget._tab_fade_effect = None
                widget.setGraphicsEffect(None)
        except RuntimeError:
            pass

    animation = QtCore.QPropertyAnimation(effect, b"opacity", widget)
    animation.setDuration(duration_ms)
    animation.setStartValue(0.0)
    animation.setEndValue(1.0)
    animation.finished.connect(_cleanup)
    QtCore.QTimer.singleShot(duration_ms + 250, _cleanup)
    animation.start()


class OverlayTabWidget(QWidget):
    """
    Кастомный виджет вкладок, где панель вкладок (QTabBar) "парит" над контентом,
    чтобы прокручиваемый контент мог плавно уходить под нее.
    Серые зоны по бокам от кнопок отсутствуют — кнопки просто висят поверх контента.
    """
    currentChanged = QtCore.pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)

        self.stack = QStackedWidget()
        self.tab_bar = QTabBar()
        self.tab_bar.setDocumentMode(True)
        self.tab_bar.setExpanding(False)
        self.tab_bar.setUsesScrollButtons(False)
        self.tab_bar.setDrawBase(False)
        self.tab_bar.installEventFilter(self)

        # Стек занимает всю площадь, tab_bar накладывается поверх
        self._grid.addWidget(self.stack, 0, 0)
        self._grid.addWidget(
            self.tab_bar, 0, 0,
            alignment=(
                QtCore.Qt.AlignmentFlag.AlignTop
                | QtCore.Qt.AlignmentFlag.AlignHCenter
            ),
        )

        self.tab_bar.currentChanged.connect(self.stack.setCurrentIndex)
        self.tab_bar.currentChanged.connect(self._fade_current_tab)
        self.tab_bar.currentChanged.connect(self.currentChanged.emit)

    def _fade_current_tab(self, index: int) -> None:
        # Фейд только при живом переключении: во время сборки вкладок
        # виджет ещё не показан, и эффект только мешал бы.
        widget = self.stack.widget(index)
        if widget is None or not self.isVisible():
            return
        _fade_in_tab(widget, TAB_FADE_MS)

    def eventFilter(self, obj, event):
        if obj is self.tab_bar and event.type() == QtCore.QEvent.Type.Wheel:
            return True
        return super().eventFilter(obj, event)

    # -- API, совместимый с QTabWidget --

    def setDocumentMode(self, mode: bool) -> None:
        self.tab_bar.setDocumentMode(mode)

    def addTab(self, widget: QWidget, label: str) -> int:
        idx = self.tab_bar.addTab(label)
        self.stack.addWidget(widget)

        # Добавляем верхний отступ, чтобы контент не прятался под tab_bar
        inner = widget
        if isinstance(widget, QScrollArea) and widget.widget():
            inner = widget.widget()

        if inner and inner.layout():
            m = inner.layout().contentsMargins()
            inner.layout().setContentsMargins(
                m.left(), m.top() + 45, m.right(), m.bottom()
            )

        return idx

    def setCurrentIndex(self, index: int) -> None:
        self.tab_bar.setCurrentIndex(index)

    def currentIndex(self) -> int:
        return self.tab_bar.currentIndex()

    def count(self) -> int:
        return self.tab_bar.count()

    def setTabText(self, index: int, text: str) -> None:
        self.tab_bar.setTabText(index, text)

    def tabText(self, index: int) -> str:
        return self.tab_bar.tabText(index)

    def setTabEnabled(self, index: int, enabled: bool) -> None:
        self.tab_bar.setTabEnabled(index, enabled)

    def isTabEnabled(self, index: int) -> bool:
        return self.tab_bar.isTabEnabled(index)

    def widget(self, index: int) -> QWidget:
        return self.stack.widget(index)
