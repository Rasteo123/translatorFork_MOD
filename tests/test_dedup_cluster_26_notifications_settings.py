"""cluster-26: единый QSettings-обработчик notifications_enabled.

До рефакторинга ``_on_notifications_toggled`` был продублирован (идентичный
код из 3 строк) в setup.py, consistency_checker.py и ai_generation.py.
Канонический обработчик — ``NotificationManager.is_enabled`` /
``NotificationManager.set_enabled`` в gemini_translator/ui/notifications.py.

Часть A — характеризационные тесты канонической реализации.
Часть B — тесты-маршрутизация: должны падать до рефакторинга (у каждого
диалога был собственный слот-копия, не обращавшийся к NotificationManager)
и проходить после (чекбоксы подключены к канонической функции напрямую).
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("GT_DISABLE_LOCAL_MODEL_DISCOVERY", "1")

import tempfile
import unittest
from unittest.mock import patch

from PyQt6 import QtCore, QtWidgets
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication, QCheckBox, QWidget

from gemini_translator.ui.notifications import NotificationManager


def _ensure_app():
    return QApplication.instance() or QApplication([])


class NotificationManagerCharacterizationTests(unittest.TestCase):
    """Часть A: поведение канонического обработчика."""

    def setUp(self):
        _ensure_app()
        self.settings = QSettings("SiberianTeam", "TranslatorFork")
        previous = self.settings.value("notifications_enabled", None)

        def _restore():
            if previous is None:
                self.settings.remove("notifications_enabled")
            else:
                self.settings.setValue("notifications_enabled", previous)
            self.settings.sync()

        self.addCleanup(_restore)

    def test_is_enabled_defaults_to_true_when_key_absent(self):
        self.settings.remove("notifications_enabled")
        self.settings.sync()
        self.assertTrue(NotificationManager.is_enabled())

    def test_set_enabled_true_is_reflected_by_is_enabled(self):
        NotificationManager.set_enabled(True)
        self.assertTrue(NotificationManager.is_enabled())

    def test_set_enabled_false_is_reflected_by_is_enabled(self):
        NotificationManager.set_enabled(False)
        self.assertFalse(NotificationManager.is_enabled())

    def test_set_enabled_uses_siberianteam_translatorfork_namespace(self):
        """Критично: тот же namespace, что читает NotificationManager.show."""
        NotificationManager.set_enabled(False)
        raw = QSettings("SiberianTeam", "TranslatorFork")
        self.assertFalse(raw.value("notifications_enabled", True, type=bool))

    def test_set_enabled_accepts_truthy_non_bool_like_a_qcheckbox_signal(self):
        """QCheckBox.toggled эмитит bool, но проверяем устойчивость к 0/1."""
        NotificationManager.set_enabled(1)
        self.assertTrue(NotificationManager.is_enabled())
        NotificationManager.set_enabled(0)
        self.assertFalse(NotificationManager.is_enabled())


class _StubKeyManagementWidget(QWidget):
    """Лёгкая замена KeyManagementWidget для сборки GenerationSessionPage._create_settings_tab."""

    active_keys_changed = QtCore.pyqtSignal()

    def __init__(self, settings_manager=None, parent=None):
        super().__init__(parent if isinstance(parent, QWidget) else None)
        self.provider_combo = QtWidgets.QComboBox()


class _StubModelSettingsWidget(QWidget):
    """Лёгкая замена ModelSettingsWidget: реальный QWidget, чтобы findChild работал сам."""

    def __init__(self, parent=None):
        super().__init__(parent if isinstance(parent, QWidget) else None)
        self.dynamic_glossary_checkbox = QCheckBox()
        self.use_jieba_glossary_checkbox = QCheckBox()
        self.segment_text_checkbox = QCheckBox()

    def set_provider_event_source(self, *_a, **_k):
        pass


class NotificationsRoutingTests(unittest.TestCase):
    """Часть B: каждый бывший центр дублирования должен звать канонический метод."""

    @classmethod
    def setUpClass(cls):
        cls.app = _ensure_app()

    def _spy_set_enabled(self):
        patcher = patch.object(NotificationManager, "set_enabled")
        spy = patcher.start()
        self.addCleanup(patcher.stop)
        return spy

    # ---- setup.py: InitialSetupPage._create_session_behavior_group -------

    def test_setup_dialog_notifications_checkbox_routes_through_canonical(self):
        from gemini_translator.ui.dialogs.setup import InitialSetupPage

        spy = self._spy_set_enabled()

        page = InitialSetupPage.__new__(InitialSetupPage)
        QWidget.__init__(page)
        page.settings_manager = None

        # Виджет должен остаться жив, пока за него держится Python-переменная
        # (иначе PyQt удаляет C++-объект и isChecked()/setChecked() падают).
        group = InitialSetupPage._create_session_behavior_group(page)  # noqa: F841

        self.assertFalse(hasattr(InitialSetupPage, "_on_notifications_toggled"))

        page.cb_notifications.setChecked(not page.cb_notifications.isChecked())
        spy.assert_called_once()
        self.assertEqual(spy.call_args.args[0], page.cb_notifications.isChecked())

    # ---- consistency_checker.py: ConsistencyValidatorPage full build ------

    def test_consistency_checker_notifications_checkbox_routes_through_canonical(self):
        from main import EventBus
        from gemini_translator.ui.dialogs.consistency_checker import ConsistencyValidatorPage
        from gemini_translator.utils.settings import SettingsManager

        self.app.event_bus = EventBus()
        settings_file = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        settings_file.close()
        self.addCleanup(lambda: os.path.exists(settings_file.name) and os.unlink(settings_file.name))
        settings_manager = SettingsManager(
            event_bus=self.app.event_bus,
            config_file=settings_file.name,
        )
        self.app.settings_manager = settings_manager
        self.app.get_settings_manager = lambda: settings_manager

        spy = self._spy_set_enabled()

        with patch(
            "gemini_translator.ui.widgets.key_management_widget.KeyManagementWidget.minimumSizeHint"
        ) as mock_min_size, patch.object(
            ConsistencyValidatorPage, "_check_for_previous_session", lambda _page: None
        ):
            from PyQt6.QtCore import QSize
            mock_min_size.return_value = QSize(800, 600)
            page = ConsistencyValidatorPage(
                [{"name": "Chapter 1", "content": "text", "path": "chapter.xhtml"}],
                settings_manager,
            )
        self.addCleanup(page.close)

        self.assertFalse(hasattr(ConsistencyValidatorPage, "_on_notifications_toggled"))

        page.cb_notifications.setChecked(not page.cb_notifications.isChecked())
        spy.assert_called_once()
        self.assertEqual(spy.call_args.args[0], page.cb_notifications.isChecked())

    # ---- ai_generation.py: GenerationSessionPage._create_settings_tab -----

    def test_ai_generation_notifications_checkbox_routes_through_canonical(self):
        from gemini_translator.ui.dialogs.glossary_dialogs import ai_generation
        from gemini_translator.ui.dialogs.glossary_dialogs.ai_generation import GenerationSessionPage

        spy = self._spy_set_enabled()

        page = GenerationSessionPage.__new__(GenerationSessionPage)
        QWidget.__init__(page)
        page.settings_manager = None
        page._update_instances_spinbox_limit = lambda *a, **k: None
        page._on_mode_changed = lambda *a, **k: None
        page._save_shared_sleep_prevention_setting = lambda *a, **k: None

        with patch.object(ai_generation, "KeyManagementWidget", _StubKeyManagementWidget), \
             patch.object(ai_generation, "ModelSettingsWidget", _StubModelSettingsWidget):
            scroll_area = GenerationSessionPage._create_settings_tab(page)  # noqa: F841

        self.assertFalse(hasattr(GenerationSessionPage, "_on_notifications_toggled"))

        page.cb_notifications.setChecked(not page.cb_notifications.isChecked())
        spy.assert_called_once()
        self.assertEqual(spy.call_args.args[0], page.cb_notifications.isChecked())


if __name__ == "__main__":
    unittest.main()
