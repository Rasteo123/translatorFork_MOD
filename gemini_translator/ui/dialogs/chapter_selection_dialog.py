# -*- coding: utf-8 -*-
"""
ChapterSelectionDialog - Диалог выбора глав для проверки согласованности.
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QListWidget,
    QListWidgetItem, QLabel, QMessageBox, QLineEdit,
    QGroupBox, QCheckBox, QDialogButtonBox
)
from PyQt6.QtCore import Qt
from typing import List, Dict, Any, Optional, Set

try:
    from ...api import config as api_config
except ImportError:
    api_config = None

from ...utils import chapter_identity as chapter_identity_utils


def set_checked_all(list_widget, checked: bool, only_visible: bool = False) -> None:
    """Выставляет CheckState всем элементам списка (cluster-61).

    Каноническая реализация триады select_all/deselect_all/invert_selection,
    ранее продублированной дословно в ChapterSelectionDialog,
    glossary_dialogs.versioning.ChapterSelectorWidget и
    ranobelib/main_window.py.

    ``only_visible=True`` пропускает элементы, скрытые фильтром/поиском
    (``item.isHidden()``) — так работает «Выбрать все» после фильтрации.
    ``only_visible=False`` (по умолчанию) применяется ко всем элементам без
    исключения — так работает «Снять все», которое должно сбрасывать выбор
    целиком, а не только у видимой части списка (поведение сохранено таким,
    каким было идентично в обеих исходных копиях).
    """
    state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
    list_widget.setUpdatesEnabled(False)
    try:
        for i in range(list_widget.count()):
            item = list_widget.item(i)
            if only_visible and item.isHidden():
                continue
            item.setCheckState(state)
    finally:
        list_widget.setUpdatesEnabled(True)


def invert_checked(list_widget, only_visible: bool = False) -> None:
    """Инвертирует CheckState всех элементов списка (cluster-61).

    См. :func:`set_checked_all` — та же каноническая триада.
    ``only_visible=True`` пропускает скрытые фильтром элементы.
    """
    list_widget.setUpdatesEnabled(False)
    try:
        for i in range(list_widget.count()):
            item = list_widget.item(i)
            if only_visible and item.isHidden():
                continue
            current = item.checkState()
            item.setCheckState(
                Qt.CheckState.Unchecked if current == Qt.CheckState.Checked
                else Qt.CheckState.Checked
            )
    finally:
        list_widget.setUpdatesEnabled(True)


class ChapterSelectionDialog(QDialog):
    """Диалог для выбора глав из проекта."""

    def __init__(self, all_chapters: List[Dict[str, Any]],
                 previous_selection: Optional[List[str]] = None,
                 parent=None):
        super().__init__(parent)
        
        self.all_chapters = all_chapters  # Все доступные главы
        self.previous_selection = set(previous_selection or [])
        
        self.setWindowTitle("Выбор глав для проверки")
        self.resize(600, 500)
        
        self._init_ui()
        self._restore_selection()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        
        # Поиск
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Поиск по названию главы...")
        self.search_box.textChanged.connect(self._filter_chapters)
        layout.addWidget(self.search_box)
        
        # Список глав
        self.chapter_list = QListWidget()
        self.chapter_list.setAlternatingRowColors(True)
        self.chapter_list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.chapter_list.itemChanged.connect(self._update_stats)
        layout.addWidget(self.chapter_list)
        
        # Заполняем список
        search_role = Qt.ItemDataRole.UserRole + 1
        self.chapter_list.setUpdatesEnabled(False)
        for chapter in self.all_chapters:
            item = QListWidgetItem(chapter['name'])
            item.setCheckState(Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, chapter)
            item.setData(search_role, item.text().casefold())
            self.chapter_list.addItem(item)
        self.chapter_list.setUpdatesEnabled(True)
        
        # Кнопки управления
        btn_layout = QHBoxLayout()
        
        select_all_btn = QPushButton("Выбрать все")
        select_all_btn.clicked.connect(self._select_all)
        btn_layout.addWidget(select_all_btn)
        
        deselect_all_btn = QPushButton("Снять все")
        deselect_all_btn.clicked.connect(self._deselect_all)
        btn_layout.addWidget(deselect_all_btn)
        
        invert_btn = QPushButton("Инвертировать")
        invert_btn.clicked.connect(self._invert_selection)
        btn_layout.addWidget(invert_btn)
        
        btn_layout.addStretch()
        
        layout.addLayout(btn_layout)
        
        # Статистика
        self.stats_label = QLabel("")
        layout.addWidget(self.stats_label)
        
        # Кнопки OK/Cancel
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | 
            QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)
        
        self._update_stats()
    
    def _restore_selection(self):
        """Восстанавливает предыдущий выбор."""
        for i in range(self.chapter_list.count()):
            item = self.chapter_list.item(i)
            chapter = item.data(Qt.ItemDataRole.UserRole)
            chapter_name = str(chapter.get('name') or "").strip()
            chapter_id = chapter_identity_utils.chapter_identity(chapter)
            if chapter_name in self.previous_selection or chapter_id in self.previous_selection:
                item.setCheckState(Qt.CheckState.Checked)
        self._update_stats()
    
    def _filter_chapters(self, text: str):
        """Фильтрует список по поиску."""
        query = text.casefold()
        search_role = Qt.ItemDataRole.UserRole + 1
        self.chapter_list.setUpdatesEnabled(False)
        for i in range(self.chapter_list.count()):
            item = self.chapter_list.item(i)
            visible = query in (item.data(search_role) or "")
            item.setHidden(not visible)
        self.chapter_list.setUpdatesEnabled(True)
    
    def _select_all(self):
        """Выбирает все видимые главы."""
        set_checked_all(self.chapter_list, True, only_visible=True)
        self._update_stats()

    def _deselect_all(self):
        """Снимает выбор со всех глав."""
        set_checked_all(self.chapter_list, False, only_visible=False)
        self._update_stats()

    def _invert_selection(self):
        """Инвертирует выбор."""
        invert_checked(self.chapter_list, only_visible=True)
        self._update_stats()
    
    def _update_stats(self):
        """Обновляет статистику выбора."""
        selected = sum(
            1 for i in range(self.chapter_list.count())
            if self.chapter_list.item(i).checkState() == Qt.CheckState.Checked
        )
        total = self.chapter_list.count()
        visible = sum(
            1 for i in range(self.chapter_list.count())
            if not self.chapter_list.item(i).isHidden()
        )
        
        # Подсчёт токенов
        selected_chars = 0
        for i in range(self.chapter_list.count()):
            item = self.chapter_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                chapter = item.data(Qt.ItemDataRole.UserRole)
                selected_chars += len(chapter.get('content', ''))
        
        est_tokens = 0
        if api_config and hasattr(api_config, 'CHARS_PER_CYRILLIC_TOKEN'):
            est_tokens = selected_chars // api_config.CHARS_PER_CYRILLIC_TOKEN
        else:
            # Fallback: approximate 4 chars per token for Cyrillic
            est_tokens = selected_chars // 4
        
        self.stats_label.setText(
            f"Выбрано: {selected}/{visible} (из {total}) | "
            f"~{est_tokens:,} токенов"
        )
    
    def get_selected_chapters(self) -> List[Dict[str, Any]]:
        """Возвращает список выбранных глав."""
        selected = []
        for i in range(self.chapter_list.count()):
            item = self.chapter_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                selected.append(item.data(Qt.ItemDataRole.UserRole))
        return selected
