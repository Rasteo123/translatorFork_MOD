# --- START OF FILE gemini_translator/ui/dialogs/setup_dialogs/dry_run_dialog.py ---

# -*- coding: utf-8 -*-
from PyQt6 import QtWidgets, QtCore, QtGui
from PyQt6.QtCore import QObject, pyqtSlot, QThread
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QTextEdit, QPushButton, QHBoxLayout


class _DialogRunner(QObject):
    """
    Внутренний класс-помощник, который живет в главном потоке
    и может безопасно показывать диалоги по запросу из рабочих потоков.
    """
    def __init__(self, parent, prompt_text):
        super().__init__()
        self.parent = parent
        self.prompt_text = prompt_text
        self.result = None

    @pyqtSlot()
    def run(self):
        """Этот слот выполняется в главном потоке."""
        dialog = DryRunPromptDialog(self.parent, self.prompt_text)
        if dialog.exec():
            self.result = dialog.get_result()
        else:
            self.result = None


class DryRunPromptDialog(QDialog):
    """
    Новый диалог для пробного запуска, который позволяет редактировать
    ответ, копировать промпт и вызывать справку.
    """
    def __init__(self, parent, prompt_text):
        super().__init__(parent)
        self.setWindowTitle("Пробный запуск: Финальный промпт и ручной ввод")
        self.setMinimumSize(800, 700)
        self.setWindowFlags(
            self.windowFlags() |
            QtCore.Qt.WindowType.WindowMinimizeButtonHint |
            QtCore.Qt.WindowType.WindowMaximizeButtonHint
        )
        
        self.is_edit_mode = False
        self.result_text = None
        self.prompt_text = prompt_text # Сохраняем исходный промпт
        
        # --- UI Elements ---
        layout = QVBoxLayout(self)
        self.view = QTextEdit()
        self.view.setReadOnly(True)
        self.view.setFont(QtGui.QFont("Consolas", 10))
        self.view.setPlainText(self.prompt_text)
        
        button_layout = QHBoxLayout()
        
        # --- Новые кнопки слева ---
        self.copy_btn = QPushButton("📋 Копипаст")
        self.copy_btn.setToolTip("Скопировать весь промпт в буфер обмена")
        
        self.help_btn = QPushButton("[?]")
        self.help_btn.setFixedSize(30, 30)
        self.help_btn.setStyleSheet("font-size: 14pt; border-radius: 15px;")
        self.help_btn.setToolTip("Открыть справку по режиму пробного запуска")
        
        button_layout.addWidget(self.copy_btn)
        button_layout.addWidget(self.help_btn)
        button_layout.addStretch()
        
        # --- Старые кнопки справа ---
        self.edit_apply_btn = QPushButton("✍️ Ручной Ответ")
        self.edit_apply_btn.setToolTip("Включить режим редактирования, чтобы вручную вставить перевод.")
        self.close_btn = QPushButton("Отмена")
        
        button_layout.addWidget(self.edit_apply_btn)
        button_layout.addWidget(self.close_btn)
        
        layout.addWidget(self.view)
        layout.addLayout(button_layout)
        
        # --- Connections ---
        self.edit_apply_btn.clicked.connect(self._toggle_mode)
        self.close_btn.clicked.connect(self.reject)
        self.copy_btn.clicked.connect(self._copy_prompt_to_clipboard)
        self.help_btn.clicked.connect(self._show_help)

    def _copy_prompt_to_clipboard(self):
        """Копирует текст из QTextEdit в буфер обмена и дает обратную связь."""
        QtWidgets.QApplication.clipboard().setText(self.prompt_text)
        original_icon = self.copy_btn.text()
        self.copy_btn.setText("✓")
        self.copy_btn.setEnabled(False)
        QtCore.QTimer.singleShot(2000, lambda: (
            self.copy_btn.setText(original_icon),
            self.copy_btn.setEnabled(True)
        ))

    def _show_help(self):
        """Открывает окно справки с переходом к нужному разделу."""
        from ....utils import markdown_viewer
        markdown_viewer.show_markdown_viewer(
            parent_window=self,
            modal=True,
            section="### 🧪 Пробный запуск (Dry Run): Ручное управление"
        )
        
    def _toggle_mode(self):
        if not self.is_edit_mode:
            # --- Включаем режим редактирования ---
            self.is_edit_mode = True
            self.view.setReadOnly(False)
            self.view.setFocus()
            
            # Очищаем поле от промпта и ставим фоновую подсказку
            self.view.clear()
            self.view.setPlaceholderText("=== вставьте сюда ваш ответ ===")
            
            # Превращаем кнопку в "Применить"
            self.edit_apply_btn.setText("✅ Применить как перевод")
            self.edit_apply_btn.setStyleSheet("background-color: #2ECC71; color: white;")
            self.edit_apply_btn.setToolTip("Использовать текст из этого поля как ответ от API и сохранить результат.")
        else:
            # --- Применяем результат ---
            self.result_text = self.view.toPlainText()
            self.accept() # Закрываем диалог с успехом

    def get_result(self):
        return self.result_text

    @staticmethod
    def get_translation(parent, prompt_text):
        """
        Статический метод-фабрика. Безопасно вызывает диалог из любого потока
        и блокирует вызывающий поток до получения результата.
        """
        app = QtWidgets.QApplication.instance()
        if QThread.currentThread() == app.thread():
            # Мы уже в главном потоке, вызываем напрямую
            dialog = DryRunPromptDialog(parent, prompt_text)
            if dialog.exec():
                return dialog.get_result()
            return None
        else:
            # Мы в рабочем потоке, используем безопасный вызов
            runner = _DialogRunner(parent, prompt_text)
            runner.moveToThread(app.thread())
            
            # Вызываем слот 'run' в главном потоке и ЖДЕМ его завершения
            QtCore.QMetaObject.invokeMethod(runner, 'run', QtCore.Qt.ConnectionType.BlockingQueuedConnection)
            
            return runner.result