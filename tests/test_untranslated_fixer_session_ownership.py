import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gemini_translator.ui.dialogs.validation_dialogs.untranslated_fixer_dialog import (
    AITranslationPage,
    UNTRANSLATED_FIXER_BACKGROUND_ROLE,
)


class _TaskManagerStub:
    def __init__(self):
        self.tasks = []
        self.clear_calls = 0

    def clear_all_queues(self):
        self.clear_calls += 1
        self.tasks.clear()


class _SessionHarness:
    _on_global_event = AITranslationPage._on_global_event
    _is_owned_session_lifecycle_event = AITranslationPage._is_owned_session_lifecycle_event
    _is_owned_session_event = AITranslationPage._is_owned_session_event

    def __init__(self, task_manager, run_id, *, suppress_popups=True):
        self.task_manager = task_manager
        self._session_run_id = run_id
        self._owned_session_id = None
        self.suppress_popups = suppress_popups
        self.translated_results = ["translated"]
        self.finish_reason = ""
        self.active_values = []
        self.finished_values = []
        self.disconnected = 0
        self.token_events = []

    def _set_ui_active(self, active):
        self.active_values.append(bool(active))

    def _finish_auto_session(self, accepted):
        self.finished_values.append(bool(accepted))

    def _disconnect_global_events(self):
        self.disconnected += 1

    def _update_apply_button(self):
        pass

    def _on_token_usage_updated(self, data):
        self.token_events.append(dict(data))


class _SettingsHarness:
    get_settings = AITranslationPage.get_settings

    def __init__(self):
        self._session_run_id = "run-42"
        self.model_settings_widget = type(
            "_ModelSettings",
            (),
            {"get_settings": lambda _self: {"model": None}},
        )()
        self.key_widget = type(
            "_KeyWidget",
            (),
            {
                "get_raw_selected_provider": lambda _self: "provider",
                "get_selected_provider": lambda _self: "provider",
                "get_active_keys": lambda _self: ["key"],
            },
        )()
        spin_type = type("_Spin", (), {"value": lambda _self: 1})
        self.rpm_spin = spin_type()
        self.concurrent_spin = spin_type()
        self.threads_spin = spin_type()


class UntranslatedFixerSessionOwnershipTests(unittest.TestCase):
    @staticmethod
    def _lifecycle_event(name, run_id, session_id="session-1"):
        return {
            "event": name,
            "session_id": session_id,
            "data": {
                "background_session": True,
                "background_role": UNTRANSLATED_FIXER_BACKGROUND_ROLE,
                "background_run_id": run_id,
                "reason": "Сессия успешно завершена",
            },
        }

    def test_delayed_stale_finish_does_not_clear_retry_queue(self):
        manager = _TaskManagerStub()
        current = _SessionHarness(manager, "current-run")
        stale = _SessionHarness(manager, "stale-run")

        current._on_global_event(self._lifecycle_event("session_started", "current-run"))
        manager.tasks = ["fixer-task"]
        current._on_global_event(self._lifecycle_event("session_finished", "current-run"))

        self.assertEqual(manager.clear_calls, 1)
        self.assertEqual(current.finished_values, [True])

        manager.tasks = ["retry-chapter-1", "retry-chapter-2"]
        stale._on_global_event(self._lifecycle_event("session_finished", "current-run"))

        self.assertEqual(manager.tasks, ["retry-chapter-1", "retry-chapter-2"])
        self.assertEqual(manager.clear_calls, 1)
        self.assertEqual(stale.finished_values, [])

    def test_task_results_are_collected_only_from_owned_session(self):
        manager = _TaskManagerStub()
        harness = _SessionHarness(manager, "run-1", suppress_popups=False)
        harness.translated_results = []
        harness._on_global_event(self._lifecycle_event("session_started", "run-1", "owned-session"))

        foreign_task_event = {
            "event": "task_finished",
            "session_id": "foreign-session",
            "data": {
                "success": True,
                "task_info": ("task-id", ("raw_text_translation",)),
                "result_data": "foreign-result",
            },
        }
        owned_task_event = {
            **foreign_task_event,
            "session_id": "owned-session",
            "data": {
                **foreign_task_event["data"],
                "result_data": "owned-result",
            },
        }

        harness._on_global_event(foreign_task_event)
        harness._on_global_event(owned_task_event)

        self.assertEqual(harness.translated_results, ["owned-result"])

    def test_session_settings_mark_fixer_as_unique_background_run(self):
        settings = _SettingsHarness().get_settings()

        self.assertIs(settings["background_session"], True)
        self.assertEqual(settings["background_role"], UNTRANSLATED_FIXER_BACKGROUND_ROLE)
        self.assertEqual(settings["background_run_id"], "run-42")


if __name__ == "__main__":
    unittest.main()
