"""cluster-33: диалог «Менеджер списков слов-исключений» собирался дважды —
gemini_translator/ui/dialogs/validation.py (TranslationValidatorPage._open_exceptions_manager)
и gemini_translator/ui/dialogs/glossary_dialogs/residue_analyzer.py
(ResidueAnalyzerPage._open_exceptions_manager) — с разными способами показа:
validation.py звал exec_dialog() (оверлей приложения), residue_analyzer.py звал
dialog.exec() напрямую (всегда нативное модальное окно, без оверлея).

Каноническая реализация — свободная функция
gemini_translator.ui.dialogs.word_exceptions_dialog.open_word_exceptions_manager(),
которая всегда показывает диалог через exec_dialog (единообразный оверлейный показ
для обоих вызывающих мест) и возвращает актуальный текст пресета при принятии
диалога или None при отмене/ошибке. Постобработка результата (обновление UI,
сообщения) остаётся в каждом вызывающем месте своей.
"""

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QDialog, QWidget

from gemini_translator.ui.dialogs import validation as validation_module
from gemini_translator.ui.dialogs.glossary_dialogs import residue_analyzer as residue_module


class _SettingsManagerStub:
    """Дублирует и app-level, и word-exceptions-специфичные методы,
    которые нужны PresetWidget + канонической функции."""

    def __init__(self):
        self.saved_text = None
        self.presets_saved = None

    # app-level (PresetWidget.__init__ достаёт их из QApplication, если
    # свои функции не переданы — здесь передаются явно, но конструктор всё
    # равно требует рабочий get_settings_manager() на QApplication)
    def load_named_prompts(self):
        return {}

    def save_named_prompts(self, payload):
        return True

    def get_custom_prompt(self):
        return ""

    def get_last_prompt_preset_name(self):
        return None

    # word-exceptions-специфичные (переданы явно в PresetWidget)
    def load_word_exceptions_presets(self):
        return {}

    def save_word_exceptions_presets(self, payload):
        self.presets_saved = dict(payload)
        return True

    def get_last_word_exceptions_text(self):
        return "старый текст исключений"

    def save_last_word_exceptions_text(self, text):
        self.saved_text = text


def _make_app():
    app = QApplication.instance() or QApplication([])
    if not hasattr(app, "get_settings_manager"):
        app.get_settings_manager = lambda: _SettingsManagerStub()
    return app


class OpenWordExceptionsManagerCharacterizationTests(unittest.TestCase):
    """(a) Характеризационные тесты канонической функции."""

    @classmethod
    def setUpClass(cls):
        cls.app = _make_app()

    def test_none_settings_manager_warns_and_returns_none(self):
        from gemini_translator.ui.dialogs.word_exceptions_dialog import (
            open_word_exceptions_manager,
        )

        parent = QWidget()
        with patch(
            "gemini_translator.ui.dialogs.word_exceptions_dialog.QMessageBox.warning"
        ) as mock_warning:
            result = open_word_exceptions_manager(parent, None)

        self.assertIsNone(result)
        mock_warning.assert_called_once()

    def test_shows_via_exec_dialog_not_native_exec(self):
        """Ключевое расхождение копий: показ обязан идти через exec_dialog
        (оверлейный), а не напрямую через QDialog.exec()."""
        from gemini_translator.ui.dialogs import word_exceptions_dialog as target

        settings_manager = _SettingsManagerStub()
        parent = QWidget()

        with patch.object(
            target, "exec_dialog", return_value=QDialog.DialogCode.Rejected
        ) as mock_exec_dialog, patch.object(
            QDialog, "exec", return_value=QDialog.DialogCode.Rejected
        ) as mock_native_exec:
            open_word_exceptions_manager = target.open_word_exceptions_manager
            open_word_exceptions_manager(parent, settings_manager)

        mock_exec_dialog.assert_called_once()
        mock_native_exec.assert_not_called()

    def test_accepted_saves_and_returns_prompt_text(self):
        from gemini_translator.ui.dialogs import word_exceptions_dialog as target

        settings_manager = _SettingsManagerStub()
        parent = QWidget()

        with patch.object(
            target, "exec_dialog", return_value=QDialog.DialogCode.Accepted
        ):
            result = target.open_word_exceptions_manager(parent, settings_manager)

        self.assertEqual(result, "старый текст исключений")
        self.assertEqual(settings_manager.saved_text, "старый текст исключений")

    def test_rejected_returns_none_without_saving(self):
        from gemini_translator.ui.dialogs import word_exceptions_dialog as target

        settings_manager = _SettingsManagerStub()
        parent = QWidget()

        with patch.object(
            target, "exec_dialog", return_value=QDialog.DialogCode.Rejected
        ):
            result = target.open_word_exceptions_manager(parent, settings_manager)

        self.assertIsNone(result)
        self.assertIsNone(settings_manager.saved_text)

    def test_custom_ok_button_text_is_applied(self):
        """residue_analyzer.py использовал текст кнопки 'Принять и
        перефильтровать' вместо дефолтного 'Принять и закрыть' — расхождение
        признано оправданным разницей хост-экранов, поэтому сохраняется как
        параметр."""
        from gemini_translator.ui.dialogs import word_exceptions_dialog as target

        settings_manager = _SettingsManagerStub()
        parent = QWidget()
        captured = {}

        def _capture(ctx, dialog):
            from PyQt6.QtWidgets import QDialogButtonBox

            box = dialog.findChild(QDialogButtonBox)
            captured["ok_text"] = box.button(
                QDialogButtonBox.StandardButton.Ok
            ).text()
            return QDialog.DialogCode.Rejected

        with patch.object(target, "exec_dialog", side_effect=_capture):
            target.open_word_exceptions_manager(
                parent, settings_manager, ok_button_text="Принять и перефильтровать"
            )

        self.assertEqual(captured["ok_text"], "Принять и перефильтровать")


class ExceptionsManagerRoutingTests(unittest.TestCase):
    """(b) Маршрутизация: оба бывших места сборки диалога обязаны звать
    каноническую функцию, а не строить свою копию.

    До рефакторинга это ПАДАЕТ (каждое место строит собственный QDialog и
    никогда не вызывает open_word_exceptions_manager). После рефакторинга
    должно ПРОХОДИТЬ.
    """

    @classmethod
    def setUpClass(cls):
        cls.app = _make_app()

    def setUp(self):
        # Защита от зависания: если место вызова ещё не перешло на
        # каноническую функцию, оно построит настоящий QDialog и вызовет
        # exec()/exec_dialog() по-старому — обеим веткам подставляем
        # немедленный Rejected, чтобы тест не повис в offscreen-режиме.
        patcher = patch.object(QDialog, "exec", return_value=QDialog.DialogCode.Rejected)
        self._native_exec_patch = patcher.start()
        self.addCleanup(patcher.stop)

    def test_validation_page_routes_through_canonical_function(self):
        fake_self = QWidget()
        fake_self.settings_manager = _SettingsManagerStub()

        with patch.object(
            validation_module,
            "open_word_exceptions_manager",
            create=True,
            return_value=None,
        ) as mock_canonical:
            validation_module.TranslationValidatorPage._open_exceptions_manager(
                fake_self
            )

        mock_canonical.assert_called_once()

    def test_residue_analyzer_page_routes_through_canonical_function(self):
        fake_self = QWidget()
        fake_self.settings_manager = _SettingsManagerStub()

        with patch.object(
            residue_module,
            "open_word_exceptions_manager",
            create=True,
            return_value=None,
        ) as mock_canonical:
            residue_module.ResidueAnalyzerPage._open_exceptions_manager(fake_self)

        mock_canonical.assert_called_once()


if __name__ == "__main__":
    unittest.main()
