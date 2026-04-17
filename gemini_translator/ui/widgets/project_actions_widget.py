# gemini_translator/ui/widgets/project_actions_widget.py

from PyQt6 import QtCore
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QPushButton
from PyQt6.QtCore import pyqtSignal

class ProjectActionsWidget(QWidget):
    """
    Виджет с кнопками для глобальных действий с проектом:
    "Собрать EPUB" и "Проекты".
    """
    # Сигналы, которые виджет отправляет родительскому окну
    build_epub_requested = pyqtSignal()
    open_history_requested = pyqtSignal()

    # --- ИСПРАВЛЕНИЕ ЗДЕСЬ: Сигнал объявлен на уровне класса ---
    sync_project_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        # Сигнал УЖЕ определен выше, здесь ничего делать не нужно
        self._init_ui()

    def _init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self.build_epub_btn = QPushButton("📚 Собрать EPUB…")
        self.build_epub_btn.setToolTip("Открыть сборщик EPUB для существующего проекта")
        self.build_epub_btn.setStyleSheet("padding: 4px 10px;")
        self.build_epub_btn.clicked.connect(self.build_epub_requested.emit)
        self.build_epub_btn.setEnabled(False)
        
        # --- НОВАЯ КНОПКА ---
        self.sync_project_btn = QPushButton("🔄 Сверить проект")
        self.sync_project_btn.setToolTip("Проверить карту проекта (translation_map.json) на наличие\nотсутствующих или незарегистрированных файлов и исправить её.")
        self.sync_project_btn.setStyleSheet("padding: 4px 10px;")
        self.sync_project_btn.clicked.connect(self.sync_project_requested)
        self.sync_project_btn.setEnabled(False)
        # --- КОНЕЦ НОВОЙ КНОПКИ ---
        
        self.projects_btn = QPushButton("📂 Проекты…")
        self.projects_btn.setToolTip("Открыть историю проектов")
        self.projects_btn.setStyleSheet("padding: 4px 10px;")
        self.projects_btn.clicked.connect(self.open_history_requested.emit)

        layout.addWidget(self.build_epub_btn)
        layout.addWidget(self.sync_project_btn) # <-- Добавляем кнопку в layout
        layout.addWidget(self.projects_btn)
        layout.addStretch()

    # ----------------------------------------------------
    # Публичные методы для управления состоянием кнопок
    # ----------------------------------------------------

    def set_build_epub_enabled(self, enabled):
        """Управляет доступностью кнопки 'Собрать EPUB'."""
        self.build_epub_btn.setEnabled(enabled)

    def set_projects_enabled(self, enabled):
        """Управляет доступностью кнопки 'Проекты'."""
        self.projects_btn.setEnabled(enabled)
        
    def set_sync_enabled(self, enabled):
        """Управляет доступностью кнопки 'Сверить проект'."""
        self.sync_project_btn.setEnabled(enabled)