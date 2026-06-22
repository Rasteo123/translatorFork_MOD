"""Home page of the navigation shell: the tool picker.

Renders the translator tool grid and emits ``tool_selected(tool_id)``. The
shell decides what each id does and pushes the selected tool page.
"""
from __future__ import annotations

from PyQt6 import QtCore, QtWidgets

from gemini_translator.ui.shell import ShellPage

# (title, description, tool_id, is_large) — mirrors StartupToolDialog.
_TOOLS = [
    ("🚀 Переводчик EPUB",
     "Основной модуль для перевода книг: многопоточная обработка глав через API "
     "Gemini/OpenRouter/GLM с контролем промпта, глоссария и пакетных задач.",
     "translator", True),
    ("✅ Валидатор переводов",
     "Интерактивная вычитка и доработка перевода: текст и HTML бок о бок, "
     "исправление найденных ошибок, пометка глав готовыми к сборке.",
     "validator", False),
    ("📚 Менеджер глоссариев",
     "Редактор терминов: AI-разрешение конфликтов или ручной пошаговый режим.",
     "glossary", False),
    ("📝 EPUB -> Rulate MD",
     "Конвертер EPUB в markdown для Rulate: выбор глав, тома, платность, экспорт.",
     "rulate_export", False),
    ("✂️ Сплиттер глав",
     "Разбивает большие главы на части для EPUB и Rulate Markdown.",
     "chapter_splitter", False),
    ("🎧 Gemini Reader",
     "Озвучивание EPUB через Gemini Live: просмотр глав, очередь воркеров, MP3.",
     "gemini_reader", False),
    ("RanobeLib Uploader",
     "Внешний загрузчик глав RanobeLib: логин и Rulate-сценарии.",
     "ranobelib_uploader", False),
    ("Qidian -> Rulate",
     "Черновик книги на Rulate: данные с Qidian, AI-перевод, жанры, теги.",
     "qidian_rulate_creator", False),
    ("Бенчмарк промптов",
     "Сравнение промптов и моделей: prompt-only, live-запуск, отчёты.",
     "prompt_benchmark", False),
]


class HomePage(ShellPage):
    page_title = ""  # home shows no Back; nav bar title stays empty

    tool_selected = QtCore.pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tool_buttons: dict[str, QtWidgets.QPushButton] = {}
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(15)

        title = QtWidgets.QLabel("<h2>Выберите основной инструмент для запуска:</h2>")
        title.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        grid = QtWidgets.QGridLayout()
        small_index = 0
        for label, description, tool_id, is_large in _TOOLS:
            cell = self._make_tool_cell(label, description, tool_id, is_large)
            if is_large:
                grid.addWidget(cell, 0, 0, 1, 2)
            else:
                row = 1 + small_index // 2
                col = small_index % 2
                small_index += 1
                grid.addWidget(cell, row, col)
        layout.addLayout(grid)
        layout.addStretch()

    def _make_tool_cell(
        self, label: str, description: str, tool_id: str, is_large: bool
    ) -> QtWidgets.QWidget:
        cell = QtWidgets.QWidget()
        cell_layout = QtWidgets.QVBoxLayout(cell)
        cell_layout.setContentsMargins(0, 0, 0, 0)

        button = QtWidgets.QPushButton(label)
        font = button.font()
        if is_large:
            button.setMinimumHeight(60)
            font.setPointSize(14)
            font.setBold(True)
        else:
            button.setMinimumHeight(45)
            font.setPointSize(11)
        button.setFont(font)
        button.clicked.connect(lambda _checked=False, tid=tool_id: self.tool_selected.emit(tid))
        self.tool_buttons[tool_id] = button

        caption = QtWidgets.QLabel(description)
        caption.setWordWrap(True)
        caption.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignHCenter | QtCore.Qt.AlignmentFlag.AlignTop
        )

        cell_layout.addWidget(button)
        cell_layout.addWidget(caption)
        return cell
