"""cluster-28: единый загрузчик булевых настроек QSettings/session-снапшота.

До рефакторинга ``_load_show_chapter_char_count_enabled`` и
``_load_queue_autosave_enabled`` в setup.py, а также
``_is_session_persistence_enabled`` в consistency_checker.py, были дословными
копиями одного и того же цикла (перебор ``load_full_session_settings`` /
``load_settings``, try/except, isinstance-проверка, bool-приведение) —
отличались только именем ключа и дефолтным значением.

Канонический хелпер — ``gemini_translator.ui.dialogs.setup.load_bool_setting``.

Часть A — характеризационные тесты канонической реализации.
Часть B — тесты-маршрутизация: должны падать до рефакторинга (у каждого
места был собственный инлайновый цикл, не обращавшийся к load_bool_setting)
и проходить после (все три метода делегируют в канонический хелпер).
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("GT_DISABLE_LOCAL_MODEL_DISCOVERY", "1")

import unittest
from unittest.mock import patch


class _SettingsStub:
    """Двойник settings_manager с настраиваемыми загрузчиками."""

    def __init__(self, *, full_session=None, plain=None, raise_full_session=False):
        self._full_session = full_session
        self._plain = plain
        self._raise_full_session = raise_full_session

    def load_full_session_settings(self):
        if self._raise_full_session:
            raise RuntimeError("boom")
        return self._full_session

    def load_settings(self):
        return self._plain


class LoadBoolSettingCharacterizationTests(unittest.TestCase):
    """Часть A: поведение канонического load_bool_setting."""

    def setUp(self):
        from gemini_translator.ui.dialogs.setup import load_bool_setting
        self.load_bool_setting = load_bool_setting

    def test_returns_default_when_settings_manager_is_none(self):
        self.assertTrue(self.load_bool_setting(None, "some_key", True))
        self.assertFalse(self.load_bool_setting(None, "some_key", False))

    def test_returns_value_from_full_session_settings_when_key_present(self):
        stub = _SettingsStub(full_session={"my_key": True}, plain={})
        self.assertTrue(self.load_bool_setting(stub, "my_key", False))

    def test_falls_back_to_load_settings_when_key_absent_from_full_session(self):
        stub = _SettingsStub(full_session={"other_key": True}, plain={"my_key": True})
        self.assertTrue(self.load_bool_setting(stub, "my_key", False))

    def test_returns_default_when_key_absent_from_both_loaders(self):
        stub = _SettingsStub(full_session={}, plain={})
        self.assertEqual(self.load_bool_setting(stub, "missing_key", True), True)
        self.assertEqual(self.load_bool_setting(stub, "missing_key", False), False)

    def test_loader_exception_is_swallowed_and_falls_back_to_next_loader(self):
        stub = _SettingsStub(raise_full_session=True, plain={"my_key": True})
        self.assertTrue(self.load_bool_setting(stub, "my_key", False))

    def test_non_dict_settings_snapshot_is_ignored(self):
        stub = _SettingsStub(full_session="not-a-dict", plain=["also", "not", "a", "dict"])
        self.assertEqual(self.load_bool_setting(stub, "my_key", True), True)

    def test_missing_loader_attributes_fall_back_to_default(self):
        class _NoLoaders:
            pass

        self.assertFalse(self.load_bool_setting(_NoLoaders(), "my_key", False))

    def test_value_is_coerced_to_bool(self):
        stub = _SettingsStub(full_session={"my_key": 1}, plain={})
        result = self.load_bool_setting(stub, "my_key", False)
        self.assertIs(result, True)

        stub_zero = _SettingsStub(full_session={"my_key": 0}, plain={})
        result_zero = self.load_bool_setting(stub_zero, "my_key", True)
        self.assertIs(result_zero, False)


class BoolSettingRoutingTests(unittest.TestCase):
    """Часть B: каждое бывшее место дублирования зовёт канонический хелпер."""

    def _patch_canonical(self, return_value):
        patcher = patch(
            "gemini_translator.ui.dialogs.setup.load_bool_setting",
            return_value=return_value,
        )
        mock = patcher.start()
        self.addCleanup(patcher.stop)
        return mock

    def test_setup_show_chapter_char_count_routes_through_canonical(self):
        from gemini_translator.ui.dialogs.setup import (
            InitialSetupPage,
            SHOW_CHAPTER_CHAR_COUNT_SETTING_KEY,
        )

        mock = self._patch_canonical(True)
        page = InitialSetupPage.__new__(InitialSetupPage)
        page.settings_manager = object()

        result = page._load_show_chapter_char_count_enabled()

        self.assertTrue(result)
        mock.assert_called_once_with(
            page.settings_manager, SHOW_CHAPTER_CHAR_COUNT_SETTING_KEY, False
        )

    def test_setup_queue_autosave_routes_through_canonical(self):
        from gemini_translator.ui.dialogs.setup import (
            InitialSetupPage,
            QUEUE_AUTOSAVE_SETTING_KEY,
        )

        mock = self._patch_canonical(False)
        page = InitialSetupPage.__new__(InitialSetupPage)
        page.settings_manager = object()

        result = page._load_queue_autosave_enabled()

        self.assertFalse(result)
        mock.assert_called_once_with(
            page.settings_manager, QUEUE_AUTOSAVE_SETTING_KEY, True
        )

    def test_consistency_checker_session_persistence_routes_through_canonical(self):
        # ConsistencyValidatorPage — QWidget; __new__() без __init__() делает
        # C++-объект недоступным для getattr (PyQt6 бросает RuntimeError).
        # Поэтому используем лёгкий дублёр-хозяин метода, как это уже делает
        # tests/test_consistency_resilience.py::_RestoreOfferHarness.
        from gemini_translator.ui.dialogs.consistency_checker import (
            ConsistencyValidatorPage,
            SESSION_PERSISTENCE_SETTING_KEY,
        )

        class _Harness:
            _is_session_persistence_enabled = (
                ConsistencyValidatorPage._is_session_persistence_enabled
            )

        mock = self._patch_canonical(False)
        harness = _Harness()
        harness.settings_manager = object()

        result = harness._is_session_persistence_enabled()

        self.assertFalse(result)
        mock.assert_called_once_with(
            harness.settings_manager, SESSION_PERSISTENCE_SETTING_KEY, True
        )

    def test_consistency_checker_handles_missing_settings_manager_attribute(self):
        """Защитная ветка consistency_checker (getattr с default None) должна сохраниться."""
        from gemini_translator.ui.dialogs.consistency_checker import (
            ConsistencyValidatorPage,
            SESSION_PERSISTENCE_SETTING_KEY,
        )

        class _Harness:
            _is_session_persistence_enabled = (
                ConsistencyValidatorPage._is_session_persistence_enabled
            )

        mock = self._patch_canonical(True)
        harness = _Harness()
        # Намеренно не задаём settings_manager вовсе.

        result = harness._is_session_persistence_enabled()

        self.assertTrue(result)
        mock.assert_called_once_with(None, SESSION_PERSISTENCE_SETTING_KEY, True)


if __name__ == "__main__":
    unittest.main()
