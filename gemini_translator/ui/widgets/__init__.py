# gemini_translator/ui/widgets/__init__.py

"""
Пакет UI виджетов для Gemini EPUB Translator.

Этот __init__.py файл "поднимает" все основные классы виджетов
на уровень пакета, позволяя импортировать их более чисто.

Например, вместо:
from gemini_translator.ui.widgets.log_widget import LogWidget

Можно использовать:
from gemini_translator.ui.widgets import LogWidget
"""

from .chapter_list_widget import ChapterListWidget
from .auto_translate_widget import AutoTranslateWidget
from .glossary_widget import GlossaryWidget
from .key_management_widget import KeyManagementWidget
from .log_widget import LogWidget
from .manual_translation_widget import ManualTranslationWidget
from .model_settings_widget import ModelSettingsWidget
from .project_actions_widget import ProjectActionsWidget
from .project_paths_widget import ProjectPathsWidget
from .preset_widget import PresetWidget
from .status_bar_widget import StatusBarWidget
from .task_management_widget import TaskManagementWidget
from .translation_options_widget import TranslationOptionsWidget
from .common_widgets import NoScrollSpinBox, NoScrollDoubleSpinBox, NoScrollComboBox # <-- Добавь импорт

# Опционально: можно определить __all__, чтобы указать,
# какие имена экспортируются при 'from .widgets import *'
__all__ = [
    'ChapterListWidget',
    'AutoTranslateWidget',
    'GlossaryWidget',
    'KeyManagementWidget',
    'LogWidget',
    'ManualTranslationWidget',
    'ModelSettingsWidget',
    'ProjectActionsWidget',
    'ProjectPathsWidget',
    'PresetWidget',
    'StatusBarWidget',
    'TaskManagementWidget',
    'TranslationOptionsWidget',
    'NoScrollSpinBox',             # <-- Добавь эту строку
    'NoScrollDoubleSpinBox',       # <-- Добавь эту строку
    'NoScrollComboBox',            # <-- Добавь эту строку
]
