import unittest
from unittest.mock import patch

from gemini_translator.ui.dialogs.setup import InitialSetupDialog


class _KeyManagementWidgetStub:
    def __init__(self, selected_provider="gemini", active_keys=None, active_by_provider=None):
        self._selected_provider = selected_provider
        self._active_keys = list(active_keys or [])
        self.current_active_keys_by_provider = dict(active_by_provider or {})

    def get_selected_provider(self):
        return self._selected_provider

    def get_active_keys(self):
        return list(self._active_keys)


class _AutoFilterRedirectHarness:
    _get_active_keys_for_provider = InitialSetupDialog._get_active_keys_for_provider
    _resolve_auto_filter_redirect_override = InitialSetupDialog._resolve_auto_filter_redirect_override

    def __init__(self, key_widget):
        self.key_management_widget = key_widget


class _TaskManagerStub:
    def __init__(self, ui_state):
        self._ui_state = list(ui_state)

    def get_ui_state_list(self):
        return list(self._ui_state)


class _EngineStub:
    def __init__(self, ui_state):
        self.task_manager = _TaskManagerStub(ui_state)


class _ProjectManagerStub:
    def get_full_map(self):
        return {}


class _SpinStub:
    def __init__(self, value):
        self._value = value

    def value(self):
        return self._value


class _TranslationOptionsWidgetStub:
    def __init__(self):
        self.chapter_compositions = {
            "Text/ch1.xhtml": {"total_size": 1000},
            "Text/ch2.xhtml": {"total_size": 1000},
        }
        self.task_size_spin = _SpinStub(1500)


class _ButtonStub:
    def __init__(self):
        self.enabled_values = []

    def setEnabled(self, value):
        self.enabled_values.append(bool(value))


class _PathsWidgetStub:
    def __init__(self):
        self.chapter_counts = []

    def update_chapters_info(self, count):
        self.chapter_counts.append(int(count))


class _TaskManagementWidgetStub:
    def __init__(self):
        self.visible_values = []

    def set_retry_filtered_button_visible(self, visible):
        self.visible_values.append(bool(visible))


class _AutoFilterPipelineHarness:
    _get_active_keys_for_provider = InitialSetupDialog._get_active_keys_for_provider
    _resolve_auto_filter_redirect_override = InitialSetupDialog._resolve_auto_filter_redirect_override
    _extract_chapters_from_payload = InitialSetupDialog._extract_chapters_from_payload
    _normalize_auto_chapters = InitialSetupDialog._normalize_auto_chapters
    _make_auto_chapter_signature = InitialSetupDialog._make_auto_chapter_signature
    _compose_auto_details = InitialSetupDialog._compose_auto_details
    _try_auto_filter_recovery = InitialSetupDialog._try_auto_filter_recovery
    _try_auto_filter_redirect_followup = InitialSetupDialog._try_auto_filter_redirect_followup

    def __init__(self, ui_state, key_widget):
        self.engine = _EngineStub(ui_state)
        self.task_manager = self.engine.task_manager
        self.project_manager = _ProjectManagerStub()
        self.translation_options_widget = _TranslationOptionsWidgetStub()
        self.selected_file = "C:/book.epub"
        self.key_management_widget = key_widget
        self.start_btn = _ButtonStub()
        self.paths_widget = _PathsWidgetStub()
        self.task_management_widget = _TaskManagementWidgetStub()
        self._auto_pending_network_retry_chapters = set()
        self._auto_filter_repack_signatures = set()
        self._auto_filter_redirect_signatures = set()
        self._auto_restart_session_override = None
        self._auto_workflow_round = 0
        self._auto_followup_running = False
        self.html_files = []
        self.logs = []
        self.prepared_tasks = []
        self.processed_results = []
        self.reset_called = False
        self.ready_called = False

    def _auto_log(self, message, force=False, **kwargs):
        entry = {"message": message, "force": force}
        entry.update(kwargs)
        self.logs.append(entry)

    def _process_filter_dialog_result(self, result):
        self.processed_results.append(dict(result))

    def _prepare_and_display_tasks(self, clean_rebuild=False, translation_options_override=None):
        self.prepared_tasks.append(
            {
                "clean_rebuild": bool(clean_rebuild),
                "chapters": list(self.html_files),
            }
        )

    def _reset_auto_workflow_state(self):
        self.reset_called = True

    def check_ready(self):
        self.ready_called = True


class AutoFilterRedirectTests(unittest.TestCase):
    def test_resolve_filter_redirect_override_uses_target_provider_keys(self):
        harness = _AutoFilterRedirectHarness(
            _KeyManagementWidgetStub(
                selected_provider="gemini",
                active_by_provider={"deepseek": ["deepseek-key-1", "deepseek-key-2"]},
            )
        )

        override, warning = harness._resolve_auto_filter_redirect_override(
            {
                "filter_redirect_enabled": True,
                "filter_redirect_provider": "deepseek",
                "filter_redirect_model": "deepseek-chat NonThink",
            }
        )

        self.assertIsNone(warning)
        self.assertEqual(override["provider"], "deepseek")
        self.assertEqual(override["api_keys"], ["deepseek-key-1", "deepseek-key-2"])
        self.assertEqual(override["model"], "deepseek-chat NonThink")
        self.assertEqual(override["model_config"]["id"], "deepseek-chat")

    def test_resolve_filter_redirect_override_warns_when_target_provider_has_no_keys(self):
        harness = _AutoFilterRedirectHarness(
            _KeyManagementWidgetStub(
                selected_provider="gemini",
                active_keys=["gemini-key-1"],
                active_by_provider={"gemini": ["gemini-key-1"]},
            )
        )

        override, warning = harness._resolve_auto_filter_redirect_override(
            {
                "filter_redirect_enabled": True,
                "filter_redirect_provider": "deepseek",
                "filter_redirect_model": "deepseek-chat NonThink",
            }
        )

        self.assertIsNone(override)
        self.assertIn("нет активной сессии/ключей", warning)
        self.assertIn("deepseek", warning)

    def test_repack_is_not_repeated_after_first_attempt_when_redirect_enabled(self):
        harness = _AutoFilterPipelineHarness(
            [
                (
                    ("task-1", ("epub", "C:/book.epub", "Text/ch2.xhtml")),
                    "error",
                    {"errors": {"CONTENT_FILTER": 1}},
                )
            ],
            _KeyManagementWidgetStub(
                selected_provider="gemini",
                active_by_provider={"deepseek": ["deepseek-key-1"]},
            ),
        )
        harness._auto_filter_repack_signatures = {("Text/ch1.xhtml", "Text/ch2.xhtml")}

        result = harness._try_auto_filter_recovery(
            {
                "filter_redirect_enabled": True,
                "filter_repack_batch_size": 3,
                "filter_repack_dilute": True,
            }
        )

        self.assertFalse(result)
        self.assertEqual(harness.processed_results, [])

    def test_redirect_runs_after_repack_without_mixing_successful_chapters(self):
        harness = _AutoFilterPipelineHarness(
            [
                (
                    ("task-1", ("epub", "C:/book.epub", "Text/ch2.xhtml")),
                    "error",
                    {"errors": {"CONTENT_FILTER": 1}},
                )
            ],
            _KeyManagementWidgetStub(
                selected_provider="gemini",
                active_by_provider={"deepseek": ["deepseek-key-1"]},
            ),
        )
        harness._auto_filter_repack_signatures = {("Text/ch1.xhtml", "Text/ch2.xhtml")}

        with patch(
            "gemini_translator.ui.dialogs.setup.QtCore.QTimer.singleShot",
            lambda interval_ms, callback: None,
        ):
            result = harness._try_auto_filter_redirect_followup(
                {
                    "filter_repack_enabled": True,
                    "filter_redirect_enabled": True,
                    "filter_redirect_provider": "deepseek",
                    "filter_redirect_model": "deepseek-chat NonThink",
                    "auto_restart_after_retry": True,
                }
            )

        self.assertTrue(result)
        self.assertEqual(harness.html_files, ["Text/ch2.xhtml"])
        self.assertEqual(
            harness.prepared_tasks,
            [{"clean_rebuild": True, "chapters": ["Text/ch2.xhtml"]}],
        )
        self.assertEqual(harness._auto_restart_session_override["provider"], "deepseek")
        self.assertEqual(harness._auto_restart_session_override["model"], "deepseek-chat NonThink")
        self.assertEqual(harness.paths_widget.chapter_counts, [1])
        self.assertEqual(harness.task_management_widget.visible_values, [False])
        self.assertEqual(harness.start_btn.enabled_values, [False])
        self.assertEqual(harness._auto_workflow_round, 1)


if __name__ == "__main__":
    unittest.main()
