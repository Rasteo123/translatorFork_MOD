# gemini_translator/ui/dialogs/word_exceptions_dialog.py
"""Единая точка сборки диалога «Менеджер списков слов-исключений».

Раньше этот диалог собирался дважды почти дословно — в
``validation.py`` (``TranslationValidatorPage._open_exceptions_manager``) и в
``glossary_dialogs/residue_analyzer.py``
(``ResidueAnalyzerPage._open_exceptions_manager``) — и копии успели разойтись
по способу показа: одна использовала ``exec_dialog`` (единообразный
оверлейный показ приложения), другая — нативный ``QDialog.exec()``.
Каноническая версия всегда показывает диалог через ``exec_dialog``, а
постобработку результата (обновление UI, сообщения) оставляет вызывающему
коду — она у обоих мест разная и оправданно.
"""

from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QMessageBox, QVBoxLayout, QWidget

from ...api import config as api_config
from ..overlay_host import exec_dialog
from ..widgets.preset_widget import PresetWidget

__all__ = ["open_word_exceptions_manager"]


def open_word_exceptions_manager(
    parent: QWidget,
    settings_manager,
    ok_button_text: str = "Принять и закрыть",
) -> "str | None":
    """Строит и показывает диалог менеджера списков слов-исключений.

    Показ всегда идёт через :func:`exec_dialog` (оверлей приложения, с
    fallback на нативный ``QDialog.exec()``, если у контекста нет
    ``OverlayHost``) — именно это раньше отличало две независимые копии
    диалога.

    :param ok_button_text: текст кнопки OK; у вызывающих мест он исторически
        разный ("Принять и закрыть" / "Принять и перефильтровать") — это
        расхождение оправдано разницей экранов и сохранено как параметр.
    :returns: актуальный текст пресета исключений, если пользователь нажал
        OK, иначе ``None`` (отмена или отсутствующий ``settings_manager``).
    """
    if settings_manager is None:
        QMessageBox.warning(parent, "Ошибка", "Менеджер настроек не инициализирован.")
        return None

    dialog = QDialog(parent)
    dialog.setWindowTitle("Менеджер списков слов-исключений")
    dialog.setMinimumSize(700, 500)
    layout = QVBoxLayout(dialog)

    exceptions_widget = PresetWidget(
        parent=dialog,
        preset_name="Список исключений",
        default_prompt_func=api_config.default_word_exceptions,
        load_presets_func=settings_manager.load_word_exceptions_presets,
        save_presets_func=settings_manager.save_word_exceptions_presets,
        get_last_text_func=settings_manager.get_last_word_exceptions_text,
    )
    exceptions_widget.load_last_session_state()
    layout.addWidget(exceptions_widget)

    button_box = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
    )
    button_box.button(QDialogButtonBox.StandardButton.Ok).setText(ok_button_text)
    button_box.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
    button_box.accepted.connect(dialog.accept)
    button_box.rejected.connect(dialog.reject)
    layout.addWidget(button_box)

    if exec_dialog(parent, dialog) == QDialog.DialogCode.Accepted:
        exceptions_widget.save_last_session_state()
        prompt = exceptions_widget.get_prompt()
        settings_manager.save_last_word_exceptions_text(prompt)
        return prompt

    return None
