# gemini_translator/ui/widgets/preset_widget.py

from PyQt6 import QtWidgets, QtCore
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QPlainTextEdit, QLabel, QMessageBox,
    QComboBox, QInputDialog, QDialog
)

from ...api import config as api_config
from ...utils.settings import SettingsManager
from .common_widgets import NoScrollComboBox

class PresetWidget(QWidget):
    """
    Универсальный виджет для управления текстовыми пресетами.
    Может работать с промптами, списками исключений и т.д.
    """
    text_changed = QtCore.pyqtSignal()
    def __init__(self, parent=None, preset_name="Пресет", default_prompt_func=None,
             load_presets_func=None, save_presets_func=None,
             get_last_text_func=None, get_last_preset_func=None,
             save_last_preset_func=None, show_default_button=True,
             builtin_presets_func=None):
        
        super().__init__(parent)
        app = QtWidgets.QApplication.instance()
        self.settings_manager = app.get_settings_manager()

        # --- НОВАЯ ГИБКАЯ КОНФИГУРАЦИЯ ---
        self.preset_name = preset_name
        self.preset_name_lower = preset_name.lower()
        
        self.get_default_prompt = default_prompt_func or (lambda: "")
        self.show_default_button = show_default_button
        self.load_presets = load_presets_func or self.settings_manager.load_named_prompts
        self.save_presets = save_presets_func or self.settings_manager.save_named_prompts
        self.load_builtin_presets = builtin_presets_func or (lambda: {})
        self.get_last_text = get_last_text_func or self.settings_manager.get_custom_prompt
        self.get_last_preset = get_last_preset_func or self.settings_manager.get_last_prompt_preset_name
        self.save_last_preset = save_last_preset_func

        self.loaded_preset_name = None
        self.loaded_preset_source = None
        self.original_preset_text = None
        self.is_preset_modified = False
        self._initial_load_done = False 
        
        self._init_ui()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        
        top_panel_layout = QHBoxLayout()
        
        self.prompt_combo = NoScrollComboBox()
        self.prompt_combo.setToolTip(f"Выберите сохраненный {self.preset_name_lower}")
        self.prompt_combo.currentTextChanged.connect(self._on_prompt_selected)
        
        self.save_as_btn = QPushButton("Сохранить как…")
        self.save_as_btn.setToolTip(f"Сохранить текущий {self.preset_name_lower} под новым именем")
        self.save_as_btn.clicked.connect(self._save_new_prompt)

        self.overwrite_btn = QPushButton("Перезаписать")
        self.overwrite_btn.setToolTip(f"Перезаписать выбранный {self.preset_name_lower} текущим текстом")
        self.overwrite_btn.clicked.connect(self._overwrite_prompt)
        
        self.revert_btn = QPushButton("↩️ Отменить")
        self.revert_btn.setToolTip("Отменить изменения и вернуть исходный текст пресета")
        self.revert_btn.clicked.connect(self._revert_changes)

        self.delete_btn = QPushButton("Удалить")
        self.delete_btn.setToolTip(f"Удалить выбранный {self.preset_name_lower}")
        self.delete_btn.clicked.connect(self._delete_prompt)

        top_panel_layout.addWidget(QLabel("Пресеты:"), 0)
        top_panel_layout.addWidget(self.prompt_combo, 1)
        top_panel_layout.addWidget(self.save_as_btn)
        top_panel_layout.addWidget(self.overwrite_btn)
        top_panel_layout.addWidget(self.revert_btn)
        top_panel_layout.addWidget(self.delete_btn)
        
        self.prompt_edit = QPlainTextEdit()
        self.prompt_edit.setPlaceholderText(f"Введите свой {self.preset_name_lower} или выберите пресет…")
        self.prompt_edit.textChanged.connect(self._on_text_changed)

        bottom_panel_layout = QHBoxLayout()
        load_default_btn = QPushButton(f"📋 Загрузить стандартный {self.preset_name_lower}")
        load_default_btn.clicked.connect(self._load_default_prompt)
        load_default_btn.setVisible(self.show_default_button)
        bottom_panel_layout.addStretch()
        bottom_panel_layout.addWidget(load_default_btn)
        
        main_layout.addLayout(top_panel_layout)
        main_layout.addWidget(self.prompt_edit)
        main_layout.addLayout(bottom_panel_layout)

    def get_prompt(self):
        return self.prompt_edit.toPlainText().strip()
        
    def set_prompt(self, text):
        self.prompt_edit.setPlainText(text)

    def _load_prompts_into_combo(self):
        self.prompt_combo.blockSignals(True)
        self.prompt_combo.clear()
        builtin_prompts = self.load_builtin_presets() or {}
        prompts = self.load_presets()
        
        self.prompt_combo.addItem(f"[Новый {self.preset_name}]", userData=None)

        for name, text in builtin_prompts.items():
            self.prompt_combo.addItem(name, userData=text)
        
        for name in sorted(prompts.keys()):
            if name in builtin_prompts:
                continue
            self.prompt_combo.addItem(name, userData=prompts[name])
            
        self.prompt_combo.setCurrentIndex(0)
        self._update_button_states()
        self.prompt_combo.blockSignals(False)

    def _is_new_prompt_selected(self):
        return self.prompt_combo.currentIndex() == 0
        
    def _on_prompt_selected(self, name):
        self.prompt_edit.blockSignals(True)
        self.is_preset_modified = False

        if self.loaded_preset_name:
            index = self.prompt_combo.findText(f"{self.loaded_preset_name}*")
            if index != -1: self.prompt_combo.setItemText(index, self.loaded_preset_name)

        if self._is_new_prompt_selected():
            self.loaded_preset_name, self.loaded_preset_source, self.original_preset_text = None, None, None
            self.prompt_edit.clear()
        else:
            builtin_prompts = self.load_builtin_presets() or {}
            prompts = self.load_presets()
            clean_name = name.replace('*', '')
            if clean_name in builtin_prompts:
                self.loaded_preset_name = clean_name
                self.loaded_preset_source = "builtin"
                self.original_preset_text = builtin_prompts[clean_name]
                self.prompt_edit.setPlainText(self.original_preset_text)
            elif clean_name in prompts:
                self.loaded_preset_name = clean_name
                self.loaded_preset_source = "user"
                self.original_preset_text = prompts[clean_name]
                self.prompt_edit.setPlainText(self.original_preset_text)
        
        self.prompt_edit.blockSignals(False)
        self._update_button_states()

    def _on_text_changed(self):
        if self.loaded_preset_name is None:
            self.is_preset_modified = False
        else:
            self.is_preset_modified = (self.prompt_edit.toPlainText() != self.original_preset_text)
        self.text_changed.emit() # <--- ДОБАВЬТЕ ЭТУ СТРОКУ
        self._update_button_states()
    
    def _revert_changes(self):
        if not self.is_preset_modified or self.original_preset_text is None:
            return
        self.prompt_edit.setPlainText(self.original_preset_text)
        self.is_preset_modified = False
        self._update_button_states()
    
    def _save_new_prompt(self):
        prompt_text = self.get_prompt()
        if not prompt_text:
            QMessageBox.warning(self, f"{self.preset_name} пуст", f"Нельзя сохранить пустой {self.preset_name_lower}.")
            return
    
        # --- НАЧАЛО ИЗМЕНЕНИЙ ---
    
        # 1. Создаем экземпляр диалогового окна
        dialog = QInputDialog(self)
        dialog.setWindowTitle(f"Сохранить новый {self.preset_name}")
        dialog.setLabelText("Введите имя для этого пресета:")
        
        # 2. Устанавливаем русский текст для кнопок
        dialog.setOkButtonText("Сохранить")
        dialog.setCancelButtonText("Отмена")
    
        # 3. Запускаем диалог и проверяем результат
        # Метод exec() возвращает True, если нажата "Сохранить", и False, если "Отмена"
        if dialog.exec() == QDialog.DialogCode.Accepted:
            name = dialog.textValue()
            # Дополнительная проверка, что имя не пустое
            if not name:
                return # Пользователь нажал "Сохранить", не введя имя
        else:
            return # Пользователь нажал "Отмена"
    
        # --- КОНЕЦ ИЗМЕНЕНИЙ ---
    
        # Дальнейшая логика остается без изменений
        prompts = self.load_presets()
        builtin_prompts = self.load_builtin_presets() or {}
        if name in builtin_prompts:
            QMessageBox.warning(self, "Имя уже существует", f"Пресет с именем '{name}' уже существует. Используйте другое имя.")
            return
        if name in prompts:
            QMessageBox.warning(self, "Имя уже существует", f"Пресет с именем '{name}' уже существует. Используйте другое имя или 'Перезаписать'.")
            return
        
        prompts[name] = prompt_text
        if self.save_presets(prompts):
            # 1. Устанавливаем новый пресет как текущий и сбрасываем флаги
            self.loaded_preset_name = name
            self.loaded_preset_source = "user"
            self.original_preset_text = prompt_text
            self.is_preset_modified = False
            # 2. Перезагружаем комбобокс и выбираем новый пресет
            self._load_prompts_into_combo()
            self.prompt_combo.setCurrentText(name)
            self._update_button_states()
        else:
            QMessageBox.warning(self, "Ошибка", "Не удалось сохранить файл с пресетами.")

    def _overwrite_prompt(self):
        if not self.loaded_preset_name: return
        if self.loaded_preset_source == "builtin":
            return
        prompt_text = self.get_prompt()
        if not prompt_text:
            QMessageBox.warning(self, f"{self.preset_name} пуст", f"Нельзя перезаписать пресет пустым текстом.")
            return

        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("Подтверждение")
        msg_box.setText(f"Вы уверены, что хотите перезаписать пресет '{self.loaded_preset_name}'?")
        msg_box.setIcon(QMessageBox.Icon.Question)
        yes_button = msg_box.addButton("Да, перезаписать", QMessageBox.ButtonRole.YesRole)
        no_button = msg_box.addButton("Нет", QMessageBox.ButtonRole.NoRole)
        msg_box.setDefaultButton(no_button)
        msg_box.exec()

        if msg_box.clickedButton() == yes_button:
            prompts = self.load_presets()
            prompts[self.loaded_preset_name] = prompt_text
            if self.save_presets(prompts):
                ## НАЧАЛО ИСПРАВЛЕНИЯ ##
                # 1. Запоминаем имя, с которым работали
                current_name = self.loaded_preset_name
                # 2. Обновляем "точку отсчета" и сбрасываем флаг "изменено"
                self.original_preset_text = prompt_text
                self.is_preset_modified = False
                # 3. Перезагружаем список в комбобоксе
                self._load_prompts_into_combo()
                # 4. Устанавливаем правильный пресет и обновляем кнопки
                self.prompt_combo.setCurrentText(current_name)
                self._update_button_states()

            else:
                QMessageBox.warning(self, "Ошибка", "Не удалось сохранить файл с пресетами.")

    def _delete_prompt(self):
        if not self.loaded_preset_name: return
        if self.loaded_preset_source == "builtin":
            return

        # --- НАЧАЛО ИЗМЕНЕНИЯ ---
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("Подтверждение")
        msg_box.setText(f"Вы уверены, что хотите удалить пресет '{self.loaded_preset_name}'?")
        msg_box.setIcon(QMessageBox.Icon.Question)
        yes_button = msg_box.addButton("Да, удалить", QMessageBox.ButtonRole.YesRole)
        no_button = msg_box.addButton("Нет", QMessageBox.ButtonRole.NoRole)
        msg_box.setDefaultButton(no_button)
        msg_box.exec()
        
        if msg_box.clickedButton() == yes_button:
        # --- КОНЕЦ ИЗМЕНЕНИЯ ---
            prompts = self.load_presets()
            if self.loaded_preset_name in prompts:
                del prompts[self.loaded_preset_name]
                if self.save_presets(prompts):
                    self.prompt_edit.clear()
                    self.loaded_preset_name = None
                    self.loaded_preset_source = None
                    self._load_prompts_into_combo()
                else:
                    QMessageBox.warning(self, "Ошибка", "Не удалось сохранить файл с пресетами.")

    def _update_button_states(self):
        is_preset_loaded = self.loaded_preset_name is not None
        is_user_preset = self.loaded_preset_source == "user"
        self.overwrite_btn.setEnabled(is_user_preset and self.is_preset_modified)
        self.delete_btn.setEnabled(is_user_preset)
        self.revert_btn.setEnabled(is_preset_loaded and self.is_preset_modified)

        self.prompt_combo.blockSignals(True)
        new_prompt_index = 0
        if self.prompt_combo.count() > new_prompt_index:
            self.prompt_combo.setItemText(new_prompt_index, f"[Новый {self.preset_name}]")
        if is_preset_loaded:
            index = self.prompt_combo.findText(self.loaded_preset_name)
            if index == -1:
                index = self.prompt_combo.findText(f"{self.loaded_preset_name}*")
            if index != -1 and index != new_prompt_index:
                new_text = f"{self.loaded_preset_name}*" if self.is_preset_modified else self.loaded_preset_name
                self.prompt_combo.setItemText(index, new_text)
        self.prompt_combo.blockSignals(False)

    def _load_default_prompt(self):
        self.prompt_edit.setPlainText(self.get_default_prompt())
        self.prompt_combo.setCurrentIndex(0)

    def load_last_session_state(self):
        # --- КЛЮЧЕВОЕ ИСПРАВЛЕНИЕ: Сначала всегда загружаем список ---
        self._load_prompts_into_combo()
        self._initial_load_done = True
        # 1. Теперь, когда список гарантированно полон, пытаемся загрузить имя пресета
        last_preset_name = self.get_last_preset()
        if last_preset_name:
            index = self.prompt_combo.findText(last_preset_name)
            if index != -1:
                # Если пресет найден, выбираем его.
                self.prompt_combo.setCurrentIndex(index)
                # _on_prompt_selected будет вызван автоматически и загрузит текст.
                return # Выходим, основная работа сделана
            else:
                # ПРЕДОХРАНИТЕЛЬ: Пресет не найден!
                print(f"[PRESET WIDGET] Предупреждение: Последний использованный пресет '{last_preset_name}' не найден. Сброс настройки.")
                # Очищаем "битую" настройку, чтобы не пытаться загрузить ее снова
                if self.save_last_preset:
                    self.save_last_preset(None)

        # 2. Если пресет не был загружен (или его нет), загружаем последний "свободный" текст
        last_text = self.get_last_text()
        if last_text:
            self.set_prompt(last_text)
    
    def get_current_preset_name(self):
        """
        Возвращает "чистое" имя текущего выбранного пресета,
        читая его напрямую из QComboBox.
        """
        # 1. Получаем текст, который сейчас виден пользователю
        current_text = self.prompt_combo.currentText()
        
        # 2. Если это заглушка "[Новый …]" или пустая строка, то пресета нет
        if self._is_new_prompt_selected() or not current_text:
            return None
            
        # 3. Убираем звездочку, если она есть, чтобы вернуть "чистое" имя
        return current_text.replace('*', '')
        
    def save_last_session_state(self):
        """Сохраняет имя текущего выбранного пресета в настройки."""
        if self.save_last_preset:
            self.save_last_preset(self.get_current_preset_name())
            
    def showEvent(self, event):
        """
        Перехватываем событие показа.
        Загружаем состояние только если это первый показ, чтобы не затереть
        ручные правки при переключении вкладок.
        """
        super().showEvent(event)
        
        if not self._initial_load_done:
            self.load_last_session_state()
        
        
