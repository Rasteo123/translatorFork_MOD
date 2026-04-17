# -*- coding: utf-8 -*-

import re

from PyQt6 import QtWidgets, QtCore
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QPlainTextEdit,
    QMessageBox, QSplitter
)

from ...api import config as api_config
from ...utils.text import safe_format
from .preset_widget import PresetWidget
from ..dialogs.setup_dialogs.dry_run_dialog import DryRunPromptDialog


class ManualTranslationWidget(QWidget):
    """
    Вкладка ручного перевода plain text через dry run диалог.
    """

    def __init__(self, parent=None, settings_manager=None, model_settings_widget=None, settings_getter=None):
        super().__init__(parent)
        app = QtWidgets.QApplication.instance()
        self.settings_manager = settings_manager or app.get_settings_manager()
        self.model_settings_widget = model_settings_widget
        self.settings_getter = settings_getter
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(8)

        info_label = QLabel(
            "Этот режим повторяет пробный запуск, но работает с чистым текстом. "
            "Приложение собирает финальный prompt, вы копируете его во внешний ИИ или используете как шаблон, "
            "а затем вставляете готовый ответ обратно без HTML-валидации."
        )
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        self.prompt_widget = PresetWidget(
            parent=self,
            preset_name="Промпт ручного перевода",
            default_prompt_func=api_config.default_manual_translation_prompt,
            load_presets_func=self.settings_manager.load_manual_translation_prompts,
            save_presets_func=self.settings_manager.save_manual_translation_prompts,
            get_last_text_func=self.settings_manager.get_last_manual_translation_prompt_text,
            get_last_preset_func=self.settings_manager.get_last_manual_translation_prompt_preset_name,
            save_last_preset_func=self.settings_manager.save_last_manual_translation_prompt_preset_name
        )
        self.prompt_widget.load_last_session_state()
        layout.addWidget(self.prompt_widget, 1)

        editors_splitter = QSplitter(self)
        editors_splitter.setOrientation(QtCore.Qt.Orientation.Vertical)

        source_container = QWidget(self)
        source_layout = QVBoxLayout(source_container)
        source_layout.setContentsMargins(0, 0, 0, 0)
        source_layout.addWidget(QLabel("Исходный текст"))

        self.source_edit = QPlainTextEdit(self)
        self.source_edit.setPlaceholderText("Вставьте сюда текст для ручного перевода…")
        source_layout.addWidget(self.source_edit)

        result_container = QWidget(self)
        result_layout = QVBoxLayout(result_container)
        result_layout.setContentsMargins(0, 0, 0, 0)
        result_layout.addWidget(QLabel("Результат"))

        self.result_edit = QPlainTextEdit(self)
        self.result_edit.setPlaceholderText("Здесь появится текст, который вы вставите из dry run диалога…")
        result_layout.addWidget(self.result_edit)

        editors_splitter.addWidget(source_container)
        editors_splitter.addWidget(result_container)
        editors_splitter.setSizes([320, 320])
        layout.addWidget(editors_splitter, 2)

        buttons_layout = QHBoxLayout()
        self.run_btn = QPushButton("✍️ Ручной перевод")
        self.copy_result_btn = QPushButton("📋 Копировать результат")
        self.swap_btn = QPushButton("⇅ Поменять местами")
        self.clear_btn = QPushButton("Очистить")

        buttons_layout.addWidget(self.run_btn)
        buttons_layout.addStretch()
        buttons_layout.addWidget(self.copy_result_btn)
        buttons_layout.addWidget(self.swap_btn)
        buttons_layout.addWidget(self.clear_btn)
        layout.addLayout(buttons_layout)

        self.run_btn.clicked.connect(self._open_manual_translation_dialog)
        self.copy_result_btn.clicked.connect(self._copy_result)
        self.swap_btn.clicked.connect(self._swap_texts)
        self.clear_btn.clicked.connect(self._clear_all)

    def _build_full_prompt_text(self, source_text: str) -> str:
        prompt_template = self.prompt_widget.get_prompt() or api_config.default_manual_translation_prompt()
        settings = self.settings_getter() if self.settings_getter else {}
        if not settings and self.model_settings_widget:
            settings = self.model_settings_widget.get_settings()

        glossary_text = ""
        app = QtWidgets.QApplication.instance()
        context_manager = getattr(app, "context_manager", None)
        if context_manager and settings:
            try:
                context_manager.update_settings(settings)
                glossary_text = context_manager.format_glossary_for_prompt(
                    text_content=source_text,
                    current_chapters_list=None
                )
            except Exception:
                glossary_text = ""

        format_examples = self._build_format_examples(source_text)
        prompt_text = safe_format(
            prompt_template,
            text=source_text,
            glossary=glossary_text,
            format_examples=format_examples
        )

        system_instruction = None
        if settings.get('use_system_instruction'):
            system_instruction = settings.get('system_instruction')

        final_output = []
        if system_instruction:
            final_output.extend([
                "====================================================",
                "          SYSTEM INSTRUCTION (СИСТЕМНАЯ ИНСТРУКЦИЯ)          ",
                "====================================================",
                system_instruction.strip(),
                "",
                "",
                "====================================================",
                "               USER PROMPT (ПРОМПТ ПОЛЬЗОВАТЕЛЯ)               ",
                "====================================================",
            ])

        final_output.append(prompt_text.strip())
        return "\n".join(final_output)

    def _build_format_examples(self, source_text: str) -> str:
        examples_db = api_config.internal_prompts().get('translation_output_examples', {})
        lang_counts = {
            'zh': len(re.findall(r'[\u4e00-\u9fff]', source_text)),
            'ko': len(re.findall(r'[\uac00-\ud7af]', source_text)),
            'jp': len(re.findall(r'[\u3040-\u309f\u30a0-\u30ff]', source_text)),
            'en': len(re.findall(r'[a-zA-Z]', source_text))
        }
        target_key = max(lang_counts, key=lang_counts.get) if any(lang_counts.values()) else 'en'
        selected_examples = list(examples_db.get(target_key) or examples_db.get('en') or examples_db.get('base') or [])
        if not selected_examples:
            return ""

        normalized = []
        for example in selected_examples:
            normalized.append(
                example
                .replace("<p>", "")
                .replace("</p>", "")
                .replace("<source_text>", "")
                .replace("</source_text>", "")
            )
        return "\n\n".join(normalized)

    def _open_manual_translation_dialog(self):
        source_text = self.source_edit.toPlainText().strip()
        if not source_text:
            QMessageBox.warning(self, "Нет текста", "Сначала вставьте исходный текст для ручного перевода.")
            return

        self.prompt_widget.save_last_session_state()
        self.settings_manager.save_last_manual_translation_prompt_text(self.prompt_widget.get_prompt())

        full_prompt_text = self._build_full_prompt_text(source_text)
        translated_text = DryRunPromptDialog.get_translation(self.window(), full_prompt_text)
        if translated_text is not None:
            self.result_edit.setPlainText(translated_text)

    def _copy_result(self):
        result_text = self.result_edit.toPlainText()
        if result_text:
            QtWidgets.QApplication.clipboard().setText(result_text)

    def _swap_texts(self):
        source_text = self.source_edit.toPlainText()
        result_text = self.result_edit.toPlainText()
        self.source_edit.setPlainText(result_text)
        self.result_edit.setPlainText(source_text)

    def _clear_all(self):
        self.source_edit.clear()
        self.result_edit.clear()
