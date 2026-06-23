"""Home page of the navigation shell: the tool picker.

Renders the translator tool cards and emits ``tool_selected(tool_id)``. The
shell decides what each id does and pushes the selected tool page.

Each tool is a flat ``QPushButton`` styled as a card (accent icon tile + title
+ description, hero card adds an "Открыть" pill). Child labels are transparent
to mouse events so the whole card is clickable, and ``tool_buttons[tool_id]``
stays a real button (``.click()`` works).
"""
from __future__ import annotations

from PyQt6 import QtCore, QtWidgets

from gemini_translator.ui.shell import ShellPage

# (icon, title, description, tool_id, is_large)
_TOOLS = [
    ("📖", "Переводчик EPUB",
     "Многопоточный перевод книг через Gemini / OpenRouter / GLM с контролем "
     "промпта, глоссария и пакетных задач.",
     "translator", True),
    ("✅", "Валидатор переводов",
     "Вычитка и доработка: текст и HTML бок о бок.",
     "validator", False),
    ("📚", "Менеджер глоссариев",
     "Редактор терминов: AI или ручной режим.",
     "glossary", False),
    ("📝", "EPUB → Rulate MD",
     "Конвертер EPUB в markdown для Rulate.",
     "rulate_export", False),
    ("✂️", "Сплиттер глав",
     "Разбивает большие главы на части.",
     "chapter_splitter", False),
    ("🎧", "Gemini Reader",
     "Озвучивание EPUB через Gemini Live.",
     "gemini_reader", False),
    ("☁️", "RanobeLib Uploader",
     "Загрузчик глав на RanobeLib.",
     "ranobelib_uploader", False),
    ("✏️", "Qidian → Rulate",
     "Черновик книги: данные Qidian + AI-перевод.",
     "qidian_rulate_creator", False),
    ("📊", "Бенчмарк промптов",
     "Сравнение промптов и моделей.",
     "prompt_benchmark", False),
]

_TRANSPARENT = QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents


class _ToolCard(QtWidgets.QFrame):
    """Clickable card: accent icon tile + title + description (+ hero pill).

    A QFrame (sizes to its layout reliably, unlike a QPushButton with child
    widgets) that emits ``clicked`` on left-release; ``click()`` is provided for
    programmatic/test activation.
    """

    clicked = QtCore.pyqtSignal()

    def __init__(self, icon, title, description, is_large, parent=None):
        super().__init__(parent)
        self.setObjectName("toolHeroCard" if is_large else "toolCard")
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)

        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(14, 13, 14, 13)
        row.setSpacing(13)

        tile = QtWidgets.QLabel(icon)
        tile.setObjectName("toolIconTile")
        tile.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        size = 46 if is_large else 38
        tile.setFixedSize(size, size)
        tile.setAttribute(_TRANSPARENT, True)
        row.addWidget(tile, 0, QtCore.Qt.AlignmentFlag.AlignTop)

        text_col = QtWidgets.QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(3)
        title_label = QtWidgets.QLabel(title)
        title_label.setObjectName("toolHeroTitle" if is_large else "toolCardTitle")
        title_label.setAttribute(_TRANSPARENT, True)
        text_col.addWidget(title_label)
        detail_label = QtWidgets.QLabel(description)
        detail_label.setObjectName("toolCardDetail")
        detail_label.setWordWrap(True)
        detail_label.setAttribute(_TRANSPARENT, True)
        text_col.addWidget(detail_label)
        row.addLayout(text_col, 1)

        if is_large:
            open_pill = QtWidgets.QLabel("Открыть")
            open_pill.setObjectName("toolOpenPill")
            open_pill.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            open_pill.setAttribute(_TRANSPARENT, True)
            row.addWidget(open_pill, 0, QtCore.Qt.AlignmentFlag.AlignVCenter)

    def click(self) -> None:
        """Programmatic activation (used by tests and keyboard)."""
        self.clicked.emit()

    def mouseReleaseEvent(self, event):
        if (
            event.button() == QtCore.Qt.MouseButton.LeftButton
            and self.rect().contains(event.position().toPoint())
        ):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class HomePage(ShellPage):
    page_title = ""  # home shows no Back; nav bar title stays empty

    tool_selected = QtCore.pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tool_buttons: dict[str, QtWidgets.QPushButton] = {}
        self._build_ui()

    def _build_ui(self) -> None:
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(26, 22, 26, 22)
        outer.setSpacing(16)

        heading = QtWidgets.QLabel("Выберите основной инструмент для запуска")
        heading.setObjectName("homeHeading")
        heading.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(heading)

        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(12)
        small_index = 0
        for icon, title, description, tool_id, is_large in _TOOLS:
            card = _ToolCard(icon, title, description, is_large)
            card.clicked.connect(
                lambda _checked=False, tid=tool_id: self.tool_selected.emit(tid)
            )
            self.tool_buttons[tool_id] = card
            if is_large:
                grid.addWidget(card, 0, 0, 1, 2)
            else:
                row = 1 + small_index // 2
                col = small_index % 2
                small_index += 1
                grid.addWidget(card, row, col)
        outer.addLayout(grid)
        outer.addStretch(1)
