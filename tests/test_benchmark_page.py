import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("GT_DISABLE_LOCAL_MODEL_DISCOVERY", "1")

from PyQt6 import QtCore, QtWidgets
from PyQt6.QtWidgets import QMessageBox

from gemini_translator.ui.pages.benchmark_page import PromptBenchmarkPage
from gemini_translator.ui.shell import ShellPage


class PromptBenchmarkPageContractTests(unittest.TestCase):
    """Contract + lifecycle-guard tests that do NOT construct the page.

    Constructing ``PromptBenchmarkPage`` builds the model combos, which block on
    local-model discovery in a headless environment without a settings manager;
    the verbatim migration is covered by review and the page works in the real
    app. So the guards are exercised via the unbound methods against stubs.
    """

    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _bind_page_methods(self, stub, *names):
        for name in names:
            setattr(stub, name, getattr(PromptBenchmarkPage, name).__get__(stub, type(stub)))

    def test_is_shell_page_subclass(self):
        self.assertTrue(issubclass(PromptBenchmarkPage, ShellPage))

    def test_page_title(self):
        self.assertEqual(PromptBenchmarkPage.page_title, "Бенчмарк промптов и моделей")

    def test_can_leave_true_when_idle(self):
        class _Stub:
            worker = None

        self.assertTrue(PromptBenchmarkPage.can_leave(_Stub()))

    def test_can_leave_blocks_while_worker_running(self):
        class _Worker:
            def isRunning(self):
                return True

        class _Stub:
            worker = _Worker()

        with patch.object(QMessageBox, "warning"):
            self.assertFalse(PromptBenchmarkPage.can_leave(_Stub()))

    def test_on_leave_saves_ui_state(self):
        calls = []

        class _Stub:
            def _save_ui_state(self):
                calls.append(True)

        PromptBenchmarkPage.on_leave(_Stub())
        self.assertEqual(calls, [True])

    def test_collect_saved_keys_ignores_active_keys_of_other_providers(self):
        # Настройки после старой ошибки окна ключей: в наборе deepseek лежат
        # ключи Gemini, в наборе openrouter — ключи NVIDIA.
        from gemini_translator.api import config as api_config

        placeholder = api_config.provider_placeholder_api_key("local")

        class _SettingsManager:
            def load_key_statuses(self):
                return [
                    {"provider": "gemini", "key": "gemini-1"},
                    {"provider": "gemini", "key": "gemini-2"},
                    {"provider": "deepseek", "key": "deepseek-1"},
                    {"provider": "nvidia", "key": "nvapi-1"},
                ]

            def load_full_session_settings(self):
                return {
                    "active_keys_by_provider": {
                        "gemini": ["gemini-2"],
                        "deepseek": ["gemini-1", "gemini-2", "deleted-key"],
                        "openrouter": ["nvapi-1"],
                        "local": [placeholder],
                    }
                }

        class _Stub:
            settings_manager = _SettingsManager()

        keys = PromptBenchmarkPage._collect_saved_keys(_Stub())

        self.assertEqual(
            keys,
            {
                "gemini": ["gemini-2"],
                "deepseek": ["deepseek-1"],
                "nvidia": ["nvapi-1"],
                "local": [placeholder],
            },
        )

    def test_compare_models_scenario_selects_one_prompt_and_all_models(self):
        class _Stub:
            _saved_run_focus = {}

        stub = _Stub()
        stub.run_cases_list = QtWidgets.QListWidget()
        stub.run_prompts_list = QtWidgets.QListWidget()
        stub.run_models_list = QtWidgets.QListWidget()
        stub.run_focus_case_combo = QtWidgets.QComboBox()
        stub.run_focus_prompt_combo = QtWidgets.QComboBox()
        stub.run_focus_model_combo = QtWidgets.QComboBox()
        stub.run_scenario_combo = QtWidgets.QComboBox()
        stub.run_scenario_combo.addItem("Сравнить модели", "compare_models")
        stub.run_hint_label = QtWidgets.QLabel()
        stub.run_estimate_label = QtWidgets.QLabel()
        stub.limit_spin = QtWidgets.QSpinBox()
        self._bind_page_methods(
            stub,
            "_set_checklist_items",
            "_refresh_run_focus_combos",
            "_set_combo_items",
            "_list_ids",
            "_apply_run_preset",
            "_run_scenario",
            "_focus_or_first",
            "_combo_value",
            "_first_id",
            "_set_checked_ids",
            "_update_run_scenario_controls",
            "_update_run_estimate",
            "_checked_count",
            "_selected_ids",
        )

        for list_widget, ids in (
            (stub.run_cases_list, ["case-a", "case-b"]),
            (stub.run_prompts_list, ["prompt-a", "prompt-b"]),
            (stub.run_models_list, ["model-a", "model-b", "model-c"]),
        ):
            stub._set_checklist_items(list_widget, ids)

        stub._refresh_run_focus_combos()
        stub.run_focus_prompt_combo.setCurrentIndex(stub.run_focus_prompt_combo.findData("prompt-b"))
        stub.run_models_list.item(1).setCheckState(QtCore.Qt.CheckState.Unchecked)

        stub._apply_run_preset()

        self.assertEqual(stub._selected_ids(stub.run_cases_list), ["case-a", "case-b"])
        self.assertEqual(stub._selected_ids(stub.run_prompts_list), ["prompt-b"])
        self.assertEqual(
            stub._selected_ids(stub.run_models_list),
            ["model-a", "model-c"],
        )
        self.assertIn("2 x 1 x 2", stub.run_estimate_label.text())

    def test_set_all_checked_refreshes_estimate_label(self):
        class _Stub:
            pass

        stub = _Stub()
        stub.run_cases_list = QtWidgets.QListWidget()
        stub.run_prompts_list = QtWidgets.QListWidget()
        stub.run_models_list = QtWidgets.QListWidget()
        stub.run_estimate_label = QtWidgets.QLabel()
        stub.limit_spin = QtWidgets.QSpinBox()
        self._bind_page_methods(
            stub,
            "_set_checklist_items",
            "_set_all_checked",
            "_update_run_estimate",
            "_checked_count",
            "_selected_ids",
        )
        for list_widget, ids in (
            (stub.run_cases_list, ["case-a"]),
            (stub.run_prompts_list, ["prompt-a"]),
            (stub.run_models_list, ["model-a", "model-b"]),
        ):
            stub._set_checklist_items(list_widget, ids)

        stub._set_all_checked(stub.run_models_list, False)

        self.assertEqual(stub.run_models_list.item(0).checkState(), QtCore.Qt.CheckState.Unchecked)
        self.assertEqual(stub.run_estimate_label.text(), "Запусков: 0  |  1 x 1 x 0")
