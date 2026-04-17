# gemini_translator/ui/widgets/project_paths_widget.py

import os
from PyQt6 import QtCore, QtWidgets
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QPushButton, QFileDialog
)
from PyQt6.QtCore import pyqtSignal
from ...utils.txt_importer import TxtImportWizardDialog

# Найти класс ProjectPathsWidget и заменить его целиком

class ProjectPathsWidget(QWidget):
    """
    Виджет для выбора исходного файла EPUB и папки для сохранения перевода.
    Инкапсулирует всю логику, связанную с UI выбора путей.
    """
    file_selected = pyqtSignal(str)
    folder_selected = pyqtSignal(str)
    chapters_reselection_requested = pyqtSignal()
    swap_file_requested = pyqtSignal() # <-- НОВЫЙ СИГНАЛ

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self):
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 5, 0, 5)
        main_layout.setSpacing(15)

        BUTTON_STYLESHEET = """
            QPushButton {
                background-color: #313643; 
                color: #A9CCE3; 
                font-size: 11pt;
                font-weight: bold; 
                padding: 5px 12px; 
                border: 1px solid #4d5666;
                border-radius: 4px; 
                min-width: 150px;
            }
            QPushButton:hover { background-color: #454d5b; }
            QPushButton:pressed { background-color: #3daee9; color: #ffffff; }
        """
        
        # Кнопка выбора файла
        self.file_path_btn = QPushButton("Выбор файла")
        self.file_path_btn.setStyleSheet(BUTTON_STYLESHEET)
        self.file_path_btn.clicked.connect(self._on_select_file)
        
        # --- НОВАЯ КНОПКА СВАПА ---
        self.btn_swap_file = QPushButton("🔄")
        self.btn_swap_file.setToolTip("Заменить исходный EPUB файл в этом проекте с сохранением совместимых переводов")
        self.btn_swap_file.setFixedSize(36, 36)
        self.btn_swap_file.setStyleSheet("""
            QPushButton { 
                background-color: #313643; border: 1px solid #4d5666; border-radius: 4px; font-size: 14pt;
            }
            QPushButton:hover { background-color: #4d5666; }
        """)
        self.btn_swap_file.setVisible(False)
        self.btn_swap_file.clicked.connect(self.swap_file_requested.emit)
        # --------------------------

        self.chapters_info_btn = QPushButton("Главы: 0") 
        self.chapters_info_btn.setStyleSheet(BUTTON_STYLESHEET)
        self.chapters_info_btn.clicked.connect(self.chapters_reselection_requested.emit)
        self.chapters_info_btn.setVisible(False)

        self.folder_path_btn = QPushButton("Выбор папки")
        self.folder_path_btn.setStyleSheet(BUTTON_STYLESHEET)
        self.folder_path_btn.clicked.connect(self._on_select_folder)
        
        # Компоновка
        file_layout = QHBoxLayout()
        file_layout.setSpacing(5)
        file_layout.addWidget(self.btn_swap_file)
        file_layout.addWidget(self.file_path_btn)
        
        main_layout.addLayout(file_layout)
        main_layout.addWidget(self.chapters_info_btn)
        main_layout.addWidget(self.folder_path_btn)
        main_layout.addStretch(1)

    def _on_select_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Выберите EPUB или TXT файл", "", "Книги (*.epub *.txt);;EPUB файлы (*.epub);;Текстовые файлы (*.txt)"
        )
        if file_path:
            if file_path.lower().endswith('.txt'):
                from ...utils.txt_importer import TxtImportWizardDialog
                output_dir = os.path.dirname(file_path)
                wizard = TxtImportWizardDialog(file_path, output_dir, self)
                if wizard.exec():
                    new_epub_path = wizard.get_generated_epub_path()
                    if new_epub_path: file_path = new_epub_path
                    else: return
                else: return
            self.file_selected.emit(file_path)

    def _on_select_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Выберите папку для перевода")
        if folder: self.folder_selected.emit(folder)
            
    def set_file_path(self, path):
        if path and os.path.exists(path):
            self.file_path_btn.setText(f"Файл: {os.path.basename(path)}")
            self.file_path_btn.setToolTip(path)
            self.chapters_info_btn.setVisible(True)
        else:
            self.file_path_btn.setText("Выбор файла")
            self.chapters_info_btn.setVisible(False)
        self._update_swap_visibility()

    def set_folder_path(self, path):
        if path and os.path.isdir(path):
            self.folder_path_btn.setText(f"Папка: {os.path.basename(path)}")
            self.folder_path_btn.setToolTip(path)
        else:
            self.folder_path_btn.setText("Выбор папки")
        self._update_swap_visibility()
            
    def update_chapters_info(self, count):
        self.chapters_info_btn.setText(f"Главы: {count}")

    def _update_swap_visibility(self):
        """Показывает кнопку свапа только если выбраны и файл, и папка."""
        has_file = "Файл:" in self.file_path_btn.text()
        has_folder = "Папка:" in self.folder_path_btn.text()
        self.btn_swap_file.setVisible(has_file and has_folder)




#########