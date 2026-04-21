from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget


class ProjectActionsWidget(QWidget):
    """
    Compact toolbar for project-level actions.
    """

    build_epub_requested = pyqtSignal()
    open_history_requested = pyqtSignal()
    sync_project_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self.card = QFrame(self)
        self.card.setObjectName("projectActionsCard")

        card_layout = QHBoxLayout(self.card)
        card_layout.setContentsMargins(10, 8, 10, 8)
        card_layout.setSpacing(8)

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(1)

        title_label = QLabel("Операции проекта")
        title_label.setObjectName("projectCardTitle")
        text_layout.addWidget(title_label)

        subtitle_label = QLabel("Сборка, синхронизация и история запусков.")
        subtitle_label.setObjectName("helperLabel")
        text_layout.addWidget(subtitle_label)

        card_layout.addLayout(text_layout)
        card_layout.addStretch(1)

        self.build_epub_btn = QPushButton("Собрать EPUB")
        self.build_epub_btn.setObjectName("projectUtilityButton")
        self.build_epub_btn.setToolTip("Открыть сборщик EPUB для текущего проекта.")
        self.build_epub_btn.clicked.connect(self.build_epub_requested.emit)
        self.build_epub_btn.setEnabled(False)

        self.sync_project_btn = QPushButton("Сверить проект")
        self.sync_project_btn.setObjectName("projectUtilityButton")
        self.sync_project_btn.setToolTip(
            "Проверить translation_map.json на отсутствующие или незарегистрированные файлы."
        )
        self.sync_project_btn.clicked.connect(self.sync_project_requested.emit)
        self.sync_project_btn.setEnabled(False)

        self.projects_btn = QPushButton("Проекты")
        self.projects_btn.setObjectName("projectUtilityButton")
        self.projects_btn.setToolTip("Открыть историю и сохраненные проекты.")
        self.projects_btn.clicked.connect(self.open_history_requested.emit)

        card_layout.addWidget(self.build_epub_btn)
        card_layout.addWidget(self.sync_project_btn)
        card_layout.addWidget(self.projects_btn)

        root_layout.addWidget(self.card)

    def set_build_epub_enabled(self, enabled):
        self.build_epub_btn.setEnabled(enabled)

    def set_projects_enabled(self, enabled):
        self.projects_btn.setEnabled(enabled)

    def set_sync_enabled(self, enabled):
        self.sync_project_btn.setEnabled(enabled)
