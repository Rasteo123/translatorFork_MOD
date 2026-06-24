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
import os
import sys
import subprocess
import requests
import tempfile
from gemini_translator.ui.shell import ShellPage
from gemini_translator.utils.updater import UpdateChecker

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

        top_row = QtWidgets.QHBoxLayout()
        self.btn_check_update = QtWidgets.QPushButton("Проверить обновления")
        self.btn_check_update.setFixedSize(160, 30)
        self.btn_check_update.clicked.connect(self.check_for_updates)
        top_row.addWidget(self.btn_check_update)
        top_row.addStretch()
        outer.addLayout(top_row)

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

    def check_for_updates(self):
        self.btn_check_update.setEnabled(False)
        self.btn_check_update.setText("Проверка...")
        
        self.updater_thread = UpdateChecker(self)
        self.updater_thread.update_available.connect(self.on_update_available)
        self.updater_thread.no_update.connect(self.on_no_update)
        self.updater_thread.error_occurred.connect(self.on_update_error)
        self.updater_thread.start()
        
    def on_no_update(self):
        self.btn_check_update.setEnabled(True)
        self.btn_check_update.setText("Проверить обновления")
        QtWidgets.QMessageBox.information(self, "Обновление", "У вас установлена последняя версия программы.")
        
    def on_update_error(self, err):
        self.btn_check_update.setEnabled(True)
        self.btn_check_update.setText("Проверить обновления")
        QtWidgets.QMessageBox.warning(self, "Ошибка", f"Не удалось проверить обновления: {err}")
        
    def on_update_available(self, version, description, download_url):
        self.btn_check_update.setEnabled(True)
        self.btn_check_update.setText("Проверить обновления")
        
        msg = QtWidgets.QMessageBox(self)
        msg.setWindowTitle("Доступно обновление")
        msg.setText(f"Доступна новая версия: {version}\n\n{description}")
        
        btn_install_now = msg.addButton("Скачать и установить", QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        btn_install_later = msg.addButton("Скачать сейчас и установить при следующем запуске приложения", QtWidgets.QMessageBox.ButtonRole.ActionRole)
        btn_ignore = msg.addButton("Игнорировать", QtWidgets.QMessageBox.ButtonRole.RejectRole)
        
        msg.exec()
        
        if msg.clickedButton() == btn_ignore:
            return
        
        install_now = (msg.clickedButton() == btn_install_now)
        
        # Запускаем загрузку
        self.download_update(download_url, install_now)
        
    def download_update(self, url, install_now):
        if not url:
            QtWidgets.QMessageBox.warning(self, "Ошибка", "Ссылка на скачивание не найдена.")
            return
            
        progress = QtWidgets.QProgressDialog("Загрузка обновления...", "Отмена", 0, 100, self)
        progress.setWindowModality(QtCore.Qt.WindowModality.WindowModal)
        progress.show()
        
        try:
            r = requests.get(url, stream=True, timeout=10)
            total_size = int(r.headers.get('content-length', 0))
            
            temp_dir = tempfile.gettempdir()
            filename = url.split('/')[-1]
            filepath = os.path.join(temp_dir, filename)
            
            with open(filepath, 'wb') as f:
                downloaded = 0
                for data in r.iter_content(chunk_size=4096):
                    if progress.wasCanceled():
                        return
                    downloaded += len(data)
                    f.write(data)
                    if total_size:
                        progress.setValue(int(100 * downloaded / total_size))
                        
            progress.setValue(100)
            
            if install_now:
                self.launch_updater(filepath)
            else:
                QtWidgets.QMessageBox.information(self, "Успех", "Обновление скачано и будет установлено при следующем запуске (или вручную).")
                
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Ошибка загрузки", f"Не удалось скачать обновление: {e}")

    def launch_updater(self, filepath):
        import subprocess
        
        # Определяем путь к updater_script
        if getattr(sys, 'frozen', False):
            # PyInstaller environment
            base_dir = os.path.dirname(sys.executable)
            updater_exe = os.path.join(base_dir, "updater_script")
            if sys.platform == "win32":
                updater_exe += ".exe"
                
            if not os.path.exists(updater_exe):
                # Fallback to direct run of installer if updater not bundled properly
                if filepath.endswith('.exe'):
                    subprocess.Popen([filepath, '/VERYSILENT', '/SUPPRESSMSGBOXES', '/FORCECLOSEAPPLICATIONS'])
                else:
                    subprocess.Popen(['open', filepath])
                sys.exit(0)
            else:
                subprocess.Popen([updater_exe, filepath, sys.executable])
                sys.exit(0)
        else:
            # Development environment
            updater_script = os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "updater_script.py")
            subprocess.Popen([sys.executable, updater_script, filepath, sys.executable])
            sys.exit(0)
        outer.addStretch(1)
