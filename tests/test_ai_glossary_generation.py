import unittest
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

from PyQt6 import QtWidgets

from gemini_translator.api import config as api_config
from gemini_translator.ui.dialogs.glossary_dialogs import ai_generation as ai_generation_module
from gemini_translator.ui.dialogs.glossary_dialogs.ai_generation import (
    GenerationSessionDialog,
    GenerationSessionPage,
    SequentialTaskProvider,
    _prepare_glossary_tasks_background,
)
from gemini_translator.ui.shell import ShellPage
from gemini_translator.ui.widgets.overlay_tab_widget import OverlayTabWidget


class GlossaryBackgroundPreparationTests(unittest.TestCase):
    def test_reads_epub_and_stores_glossary_batches(self):
        class _TaskManager:
            def __init__(self):
                self.tasks = None

            def set_pending_tasks(self, tasks):
                self.tasks = list(tasks)

        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = Path(temp_dir) / "book.epub"
            with zipfile.ZipFile(epub_path, "w") as archive:
                archive.writestr("Text/one.xhtml", "<html><body><p>中文章节</p></body></html>")
                archive.writestr("Text/two.xhtml", "<html><body><p>Second chapter</p></body></html>")

            manager = _TaskManager()
            result = _prepare_glossary_tasks_background(
                str(epub_path),
                ["Text/one.xhtml", "Text/two.xhtml"],
                {
                    "use_batching": True,
                    "chunking": False,
                    "sequential_translation": False,
                    "task_size_limit": 100000,
                    "task_size_unit": "chars",
                    "file_path": str(epub_path),
                },
                manager,
            )

        self.assertTrue(result["ok"])
        self.assertTrue(result["is_any_cjk"])
        self.assertEqual(result["task_count"], 1)
        self.assertEqual(manager.tasks[0][0], "glossary_batch_task")
        self.assertEqual(
            tuple(manager.tasks[0][2]),
            ("Text/one.xhtml", "Text/two.xhtml"),
        )

    def test_deferred_page_initialization_reports_error_without_escaping_qt_slot(self):
        class _Harness:
            html_files = ["Text/one.xhtml"]
            _pending_new_terms_limit = None
            reported = []

            class _Button:
                def setText(self, _text):
                    return None

            reselect_chapters_btn = _Button()

            def _calculate_optimal_batch_size(self):
                raise RuntimeError("windows deferred initialization failed")

            def _rebuild_glossary_tasks(self):
                raise AssertionError("task rebuild must not run after initialization failure")

            def _report_glossary_error(self, context, error):
                self.reported.append((context, error))

        harness = _Harness()
        GenerationSessionPage._deferred_initial_load(harness)

        self.assertEqual(len(harness.reported), 1)
        self.assertIn("подготовить", harness.reported[0][0].lower())
        self.assertIsInstance(harness.reported[0][1], RuntimeError)


class _PipelineSignalStub:
    def __init__(self):
        self.emitted = []

    def emit(self, *args):
        self.emitted.append(args)


class _PipelinePageStub:
    def __init__(self):
        self.enabled = True

    def setEnabled(self, enabled):
        self.enabled = bool(enabled)


class _PipelineTabsStub:
    def __init__(self, count=5):
        self.pages = [_PipelinePageStub() for _ in range(count)]

    def count(self):
        return len(self.pages)

    def widget(self, index):
        return self.pages[index]


class _PipelineGlossaryStub:
    def __init__(self):
        self.controls_enabled = True

    def commit_active_editor(self):
        return None

    def set_controls_enabled(self, enabled):
        self.controls_enabled = bool(enabled)


class _PipelinePreparationHarness:
    _start_next_pipeline_step = GenerationSessionPage._start_next_pipeline_step
    _on_glossary_rebuild_finished = GenerationSessionPage._on_glossary_rebuild_finished
    _set_pipeline_preparation_ui_locked = (
        GenerationSessionPage._set_pipeline_preparation_ui_locked
    )
    _handle_pipeline_task_preparation_finished = (
        GenerationSessionPage._handle_pipeline_task_preparation_finished
    )
    _handle_pipeline_session_finished = GenerationSessionPage._handle_pipeline_session_finished
    _finalize_pipeline_run = GenerationSessionPage._finalize_pipeline_run

    def __init__(self):
        step = ai_generation_module.create_step_from_settings(
            {"temperature": 0.3, "merge_mode": "supplement"},
            name="Дополнение",
        )
        self.pipeline_run = ai_generation_module.GlossaryPipelineRun([step])
        self.pipeline_run.start()
        self._pipeline_active_step_id = None
        self._pipeline_waiting_for_next_step = False
        self._pipeline_stop_requested = False
        self._pending_glossary_rebuild_request = None
        self._glossary_rebuild_worker = None
        self._is_glossary_rebuilding = False
        self.task_preparation_finished = _PipelineSignalStub()
        self.model_settings_widget = SimpleNamespace(
            update_cjk_options_availability=lambda **_kwargs: None,
        )
        self.instances_spin = SimpleNamespace(value=lambda: 1)
        self.sequential_mode_checkbox = SimpleNamespace(isChecked=lambda: False)
        self.tabs = _PipelineTabsStub()
        self.glossary_widget = _PipelineGlossaryStub()
        self.live_session_settings = {
            "provider": "step-provider",
            "initial_glossary_list": [],
            "glossary_generation_prompt": "step prompt",
        }
        self.rebuild_calls = 0
        self.session_start_calls = 0
        self.started_session_settings = []
        self.pipeline_logs = []

    def _select_pipeline_step(self, _step_id):
        return None

    def _refresh_pipeline_table(self):
        return None

    def _update_pipeline_buttons_state(self):
        return None

    def _append_pipeline_log(self, message, step_id=None):
        self.pipeline_run.append_log(message, step_id=step_id)

    def _post_event(self, _event_name, payload):
        self.pipeline_logs.append(payload.get("message", ""))

    def _apply_full_ui_settings(self, _settings):
        return None

    def _rebuild_glossary_tasks(self):
        self.rebuild_calls += 1

    def _get_common_settings(self):
        return dict(self.live_session_settings)

    def get_merge_mode(self):
        return "supplement"

    def _start_session(self, settings_override=None, sequential_mode_override=None):
        self.session_start_calls += 1
        settings = settings_override or self._get_common_settings()
        self.started_session_settings.append(
            (dict(settings), sequential_mode_override)
        )

    def _set_glossary_rebuild_busy(self, busy):
        self._is_glossary_rebuilding = bool(busy)

    def isVisible(self):
        return False

    def finish_preparation(self, result):
        worker = SimpleNamespace(
            result=dict(result),
            _glossary_rebuild_generation=1,
            deleteLater=lambda: None,
        )
        self._glossary_rebuild_worker = worker
        self._on_glossary_rebuild_finished(worker)


class GlossaryPipelinePreparationTests(unittest.TestCase):
    def test_pipeline_starts_session_only_after_task_preparation_succeeds(self):
        harness = _PipelinePreparationHarness()

        harness._start_next_pipeline_step()

        self.assertEqual(harness.rebuild_calls, 1)
        self.assertEqual(harness.session_start_calls, 0)

        harness.finish_preparation({"ok": True, "task_count": 1, "is_any_cjk": False})

        self.assertEqual(harness.session_start_calls, 1)
        self.assertEqual(harness.task_preparation_finished.emitted, [(True, "")])

    def test_pipeline_marks_step_failed_when_task_preparation_fails(self):
        harness = _PipelinePreparationHarness()

        harness._start_next_pipeline_step()
        harness.finish_preparation({"ok": False, "error": "broken epub"})

        self.assertEqual(harness.session_start_calls, 0)
        self.assertEqual(
            harness.pipeline_run.status,
            ai_generation_module.PIPELINE_STATUS_FAILED,
        )
        self.assertIsNone(harness._pipeline_active_step_id)
        self.assertIn("broken epub", harness.pipeline_run.last_reason)
        self.assertEqual(
            harness.task_preparation_finished.emitted,
            [(False, "broken epub")],
        )

    def test_pipeline_launch_uses_step_settings_snapshot_after_ui_changes(self):
        harness = _PipelinePreparationHarness()

        harness._start_next_pipeline_step()
        harness.live_session_settings["provider"] = "edited-during-preparation"
        harness.live_session_settings["glossary_generation_prompt"] = "edited prompt"
        harness.finish_preparation({"ok": True, "task_count": 1, "is_any_cjk": False})

        started_settings, sequential_mode = harness.started_session_settings[0]
        self.assertEqual(started_settings["provider"], "step-provider")
        self.assertEqual(started_settings["glossary_generation_prompt"], "step prompt")
        self.assertFalse(sequential_mode)

    def test_pipeline_locks_editable_controls_while_tasks_are_prepared(self):
        harness = _PipelinePreparationHarness()

        harness._start_next_pipeline_step()

        self.assertTrue(all(not page.enabled for page in harness.tabs.pages[:-1]))
        self.assertTrue(harness.tabs.pages[-1].enabled)
        self.assertFalse(harness.glossary_widget.controls_enabled)

        harness.finish_preparation({"ok": True, "task_count": 1, "is_any_cjk": False})

        self.assertTrue(all(page.enabled for page in harness.tabs.pages))
        self.assertTrue(harness.glossary_widget.controls_enabled)


class _LineEditStub:
    def __init__(self):
        self.textEdited = _SignalStub()


class _SignalStub:
    def __init__(self):
        self.emitted = []

    def connect(self, _callback):
        return None

    def emit(self, event):
        self.emitted.append(event)


class _EventBusStub:
    def __init__(self):
        self.event_posted = _SignalStub()
        self._data_store = {}

    def set_data(self, key, value):
        self._data_store[key] = value

    def get_data(self, key, default=None):
        return self._data_store.get(key, default)

    def pop_data(self, key, default=None):
        return self._data_store.pop(key, default)


class _KeyWidgetStub:
    def __init__(self):
        self.provider_id = None
        self.active_keys = []

    def set_active_keys_for_provider(self, provider_id, active_keys):
        self.provider_id = provider_id
        self.active_keys = list(active_keys or [])

    def _load_and_refresh_keys(self):
        return None

    def get_selected_provider(self):
        return self.provider_id

    def get_active_keys(self):
        return list(self.active_keys)


class _SpinBoxStub:
    def __init__(self):
        self._minimum = 1
        self._maximum = 1
        self._value = 1
        self.valueChanged = _SignalStub()
        self._line_edit = _LineEditStub()

    def minimum(self):
        return self._minimum

    def maximum(self):
        return self._maximum

    def setMinimum(self, value):
        self._minimum = int(value)
        if self._value < self._minimum:
            self._value = self._minimum

    def setMaximum(self, value):
        self._maximum = int(value)
        if self._value > self._maximum:
            self._value = self._maximum

    def setRange(self, minimum, maximum):
        self._minimum = int(minimum)
        self.setMaximum(maximum)

    def setValue(self, value):
        self._value = int(value)

    def value(self):
        return self._value

    def blockSignals(self, _value):
        return None

    def lineEdit(self):
        return self._line_edit


class _ModelSettingsWidgetStub:
    def __init__(self):
        self.received_settings = None

    def set_settings(self, settings):
        self.received_settings = dict(settings)

    def get_settings(self):
        return {"model": "saved-model"}


class _TranslationOptionsWidgetStub:
    def __init__(self):
        self.received_settings = None

    def set_settings(self, settings):
        self.received_settings = dict(settings)

    def get_settings(self):
        return {"use_batching": True}


class _PromptWidgetStub:
    def __init__(self):
        self.prompt = None

    def set_prompt(self, prompt):
        self.prompt = prompt

    def get_prompt(self):
        return self.prompt or "prompt"


class _CheckboxStub:
    def __init__(self):
        self.checked = False

    def setChecked(self, value):
        self.checked = bool(value)

    def isChecked(self):
        return self.checked


class _GlossaryWidgetStub:
    def get_glossary(self):
        return []


class _SettingsManagerStub:
    def __init__(self, saved=None):
        self.saved = dict(saved or {})
        self.full_session = {}
        self.persisted = None
        self.saved_full_session = None

    def get_last_glossary_generation_settings(self):
        return dict(self.saved)

    def save_last_glossary_generation_settings(self, settings):
        self.persisted = dict(settings)
        return True

    def load_full_session_settings(self):
        return dict(self.full_session)

    def save_full_session_settings(self, settings):
        self.saved_full_session = dict(settings)
        self.full_session = dict(settings)
        return True


class _GenerationSettingsHarness:
    _apply_initial_settings = GenerationSessionDialog._apply_initial_settings
    _apply_instances_value = GenerationSessionDialog._apply_instances_value
    _apply_new_terms_limit_value = GenerationSessionDialog._apply_new_terms_limit_value
    _set_new_terms_limit_value = GenerationSessionDialog._set_new_terms_limit_value
    _mark_new_terms_limit_user_defined = GenerationSessionDialog._mark_new_terms_limit_user_defined
    _new_terms_limit_user_defined_from_settings = GenerationSessionDialog._new_terms_limit_user_defined_from_settings
    _merge_initial_ui_settings = GenerationSessionDialog._merge_initial_ui_settings
    _save_persistent_ui_settings = GenerationSessionDialog._save_persistent_ui_settings
    _save_shared_sleep_prevention_setting = GenerationSessionDialog._save_shared_sleep_prevention_setting
    _get_available_session_capacity = GenerationSessionDialog._get_available_session_capacity
    _update_instances_spinbox_limit = GenerationSessionDialog._update_instances_spinbox_limit
    _update_new_terms_limit_from_current_size = GenerationSessionDialog._update_new_terms_limit_from_current_size
    round_up_to_tens = GenerationSessionDialog.round_up_to_tens
    _get_common_settings = GenerationSessionDialog._get_common_settings
    _get_full_ui_settings = GenerationSessionDialog._get_full_ui_settings

    def __init__(self):
        self.settings_manager = _SettingsManagerStub()
        self._restore_saved_ui_settings = True
        self._persist_ui_settings = True
        self._pending_new_terms_limit = None
        self._pending_new_terms_limit_user_defined = True
        self._new_terms_limit_user_defined = False
        self._changing_new_terms_limit_programmatically = False
        self.key_widget = _KeyWidgetStub()
        self.instances_spin = _SpinBoxStub()
        self.new_terms_limit_spin = _SpinBoxStub()
        self.new_terms_limit_spin.setRange(0, 1000)
        self.model_settings_widget = _ModelSettingsWidgetStub()
        self.translation_options_widget = _TranslationOptionsWidgetStub()
        self.prompt_widget = _PromptWidgetStub()
        self.pipeline_enabled_checkbox = _CheckboxStub()
        self.sequential_mode_checkbox = _CheckboxStub()
        self.send_notes_checkbox = _CheckboxStub()
        self.prevent_sleep_checkbox = _CheckboxStub()
        self.ai_mode_update_radio = _CheckboxStub()
        self.ai_mode_supplement_radio = _CheckboxStub()
        self.ai_mode_accumulate_radio = _CheckboxStub()
        self.glossary_widget = _GlossaryWidgetStub()
        self.epub_path = "/tmp/book.epub"
        self.pipeline_steps = []
        self.pipeline_replaced_with = None
        self.dependent_widgets_updated = 0
        self.start_button_updates = 0
        self.batch_mode_forced = 0
        self.task_size_override = None
        self.sequential_widget_updates = []

    def _force_glossary_batch_mode(self):
        self.batch_mode_forced += 1

    def _apply_glossary_task_size_override(self, size_limit=None, reason=None):
        self.task_size_override = (size_limit, reason)

    def _replace_pipeline_steps(self, steps):
        self.pipeline_replaced_with = steps

    def _update_dependent_widgets(self):
        self.dependent_widgets_updated += 1

    def _update_start_button_state(self):
        self.start_button_updates += 1

    def _update_sequential_mode_widgets(self, is_sequential):
        self.sequential_widget_updates.append(bool(is_sequential))

    def get_merge_mode(self):
        if self.ai_mode_accumulate_radio.isChecked():
            return "accumulate"
        if self.ai_mode_update_radio.isChecked():
            return "update"
        return "supplement"


class _InfoLabelStub:
    def __init__(self):
        self.text = None

    def setText(self, value):
        self.text = value


class _TaskSizeSpinStub:
    def __init__(self, value=12000, maximum=350000):
        self._value = int(value)
        self._maximum = int(maximum)

    def value(self):
        return self._value

    def maximum(self):
        return self._maximum

    def setValue(self, value):
        self._value = int(value)


class _TranslationOptionsForBatchSizeStub:
    def __init__(self, value=12000, user_defined=False):
        self.task_size_spin = _TaskSizeSpinStub(value=value)
        self.info_label = _InfoLabelStub()
        self.user_defined = bool(user_defined)
        self.info_updates = 0

    def is_task_size_user_defined(self):
        return self.user_defined

    def set_task_size_limit(self, value, *, user_defined=False):
        self.task_size_spin.setValue(value)
        self.user_defined = bool(user_defined)

    def _update_info_text(self):
        self.info_updates += 1


class _ModelSettingsForBatchSizeStub:
    def get_settings(self):
        return {"model": "test-model"}


class _GlossaryBatchSizeHarness:
    _calculate_optimal_batch_size = GenerationSessionDialog._calculate_optimal_batch_size

    def __init__(self, user_defined=False):
        self.model_settings_widget = _ModelSettingsForBatchSizeStub()
        self.translation_options_widget = _TranslationOptionsForBatchSizeStub(
            value=15404,
            user_defined=user_defined,
        )
        self._glossary_task_size_locked = False
        self._glossary_task_size_lock_reason = None
        self.new_terms_limit_updates = 0

    def _update_new_terms_limit_from_current_size(self):
        self.new_terms_limit_updates += 1


class _BottomStatusBarHarness:
    _create_bottom_status_bar = GenerationSessionDialog._create_bottom_status_bar

    def __init__(self):
        self.bus = object()
        self.engine = object()


class _StatusBarStub:
    def __init__(self, parent=None, event_bus=None, engine=None):
        self.parent = parent
        self.event_bus = event_bus
        self.engine = engine


class _SoftStopHarness:
    _emit_to_bus = staticmethod(GenerationSessionDialog._emit_to_bus)
    _build_event = GenerationSessionDialog._build_event
    _on_soft_stop_clicked = GenerationSessionDialog._on_soft_stop_clicked
    _post_event = GenerationSessionDialog._post_event

    def __init__(self):
        self.bus = _EventBusStub()
        self.engine = SimpleNamespace(session_id="session-1")
        self.orchestrator = None
        self._pipeline_stop_requested = False
        self.is_soft_stopping = False
        self.soft_stop_btn = QtWidgets.QPushButton("Завершить плавно")
        self.hard_stop_btn = QtWidgets.QPushButton("❌ Прервать")


class _HardStopEngineStub:
    def __init__(self):
        self.session_id = "session-1"
        self.cancel_reasons = []

    def cancel_translation(self, reason=""):
        self.cancel_reasons.append(reason)
        self.session_id = None


class _HardStopHarness:
    _emit_to_bus = staticmethod(GenerationSessionDialog._emit_to_bus)
    _build_event = GenerationSessionDialog._build_event
    _on_hard_stop_clicked = GenerationSessionDialog._on_hard_stop_clicked
    _request_immediate_engine_cancel = GenerationSessionPage._request_immediate_engine_cancel
    _post_event_deferred = GenerationSessionDialog._post_event_deferred
    _finish_forced_interrupt_close = GenerationSessionPage._finish_forced_interrupt_close
    reject = GenerationSessionPage.reject
    _post_event = GenerationSessionDialog._post_event

    def __init__(self):
        self.bus = _EventBusStub()
        self.engine = _HardStopEngineStub()
        self.orchestrator = None
        self._pipeline_stop_requested = False
        self.force_exit_on_interrupt = False
        self.hard_stop_btn = QtWidgets.QPushButton("❌ Прервать")
        self.soft_stop_btn = QtWidgets.QPushButton("Завершить плавно")
        self.apply_btn = QtWidgets.QPushButton("Применить")
        self.apply_btn.setVisible(False)
        self.ui_active_states = []
        self.saved_settings = 0
        self.cleanup_calls = []
        self.result_ready = _SignalStub()

    def _set_ui_active(self, active):
        self.ui_active_states.append(active)

    def _save_persistent_ui_settings(self):
        self.saved_settings += 1

    def _cleanup(self, keep_recovery_file=False):
        self.cleanup_calls.append(keep_recovery_file)


class _ProviderComboStub:
    def __init__(self):
        self.currentIndexChanged = _SignalStub()

    def currentIndex(self):
        return 0


class _GenerationLayoutHarness(GenerationSessionPage):
    def __init__(self):
        ShellPage.__init__(self)
        self.bus = object()
        self.engine = object()


class AiGlossaryGenerationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_generation_page_layout_uses_plain_tabs_not_translator_overlay(self):
        created_sections = []

        def section_factory(name):
            def _factory(page):
                created_sections.append(name)
                widget = QtWidgets.QWidget()
                widget.setObjectName(name)
                return widget
            return _factory

        def settings_factory(page):
            page.key_widget = SimpleNamespace(
                active_keys_changed=_SignalStub(),
                provider_combo=_ProviderComboStub(),
            )
            page.glossary_widget = SimpleNamespace(glossary_changed=_SignalStub())
            return section_factory("settings")(page)

        with patch.object(GenerationSessionPage, "_create_settings_tab", settings_factory), \
             patch.object(GenerationSessionPage, "_create_tasks_tab", section_factory("tasks")), \
             patch.object(GenerationSessionPage, "_create_pipeline_tab", section_factory("pipeline")), \
             patch.object(GenerationSessionPage, "_create_prompt_tab", section_factory("prompt")), \
             patch.object(GenerationSessionPage, "_create_results_tab", section_factory("results")), \
             patch.object(GenerationSessionPage, "_create_bottom_status_bar", lambda page: QtWidgets.QWidget()):
            page = _GenerationLayoutHarness()
            self.addCleanup(page.deleteLater)
            page._init_ui()

        self.assertIsInstance(page.tabs, QtWidgets.QTabWidget)
        self.assertEqual(page.findChildren(OverlayTabWidget), [])
        self.assertEqual(
            created_sections,
            ["settings", "tasks", "pipeline", "prompt", "results"],
        )

    def _provider_for_finish_tests(self):
        bus = _EventBusStub()
        provider = SequentialTaskProvider(
            settings_getter=lambda: {},
            event_bus=bus,
            translate_engine=SimpleNamespace(task_manager=None, session_id=None),
        )
        provider._is_running = True
        bus.set_data(provider.MANAGED_SESSION_FLAG_KEY, True)
        return provider, bus

    def test_sequential_provider_success_does_not_request_manual_stop(self):
        provider, bus = self._provider_for_finish_tests()

        provider._finish_session(was_cancelled=False)

        event_names = [event["event"] for event in bus.event_posted.emitted]
        self.assertIn("managed_session_completed", event_names)
        self.assertIn("generation_finished", event_names)
        self.assertNotIn("manual_stop_requested", event_names)
        self.assertFalse(bus.get_data(provider.MANAGED_SESSION_FLAG_KEY, False))

    def test_sequential_provider_cancel_still_requests_manual_stop(self):
        provider, bus = self._provider_for_finish_tests()

        provider._finish_session(was_cancelled=True)

        event_names = [event["event"] for event in bus.event_posted.emitted]
        self.assertIn("manual_stop_requested", event_names)
        self.assertIn("generation_finished", event_names)
        self.assertNotIn("managed_session_completed", event_names)

    def test_soft_stop_requests_engine_stop_in_parallel_mode(self):
        harness = _SoftStopHarness()
        self.addCleanup(harness.soft_stop_btn.deleteLater)
        self.addCleanup(harness.hard_stop_btn.deleteLater)

        harness._on_soft_stop_clicked()

        event_names = [event["event"] for event in harness.bus.event_posted.emitted]
        self.assertIn("soft_stop_requested", event_names)
        self.assertTrue(harness.is_soft_stopping)
        self.assertTrue(harness._pipeline_stop_requested)
        self.assertFalse(harness.soft_stop_btn.isEnabled())
        self.assertTrue(harness.hard_stop_btn.isEnabled())

    def test_hard_stop_requests_engine_cancel_without_blocking_ui(self):
        harness = _HardStopHarness()
        self.addCleanup(harness.soft_stop_btn.deleteLater)
        self.addCleanup(harness.hard_stop_btn.deleteLater)
        self.addCleanup(harness.apply_btn.deleteLater)

        harness._on_hard_stop_clicked()

        self.assertEqual(harness.engine.cancel_reasons, [])
        self.assertEqual(harness.ui_active_states, [False])
        self.assertTrue(harness._pipeline_stop_requested)

        self.app.processEvents()
        event_names = [event["event"] for event in harness.bus.event_posted.emitted]
        self.assertIn("manual_stop_requested", event_names)

    def test_reject_while_running_requests_stop_and_closes_without_waiting_for_session_finished(self):
        harness = _HardStopHarness()
        self.addCleanup(harness.soft_stop_btn.deleteLater)
        self.addCleanup(harness.hard_stop_btn.deleteLater)
        self.addCleanup(harness.apply_btn.deleteLater)

        harness.reject()

        self.assertEqual(harness.cleanup_calls, [False])
        self.assertEqual(harness.result_ready.emitted, [False])
        self.assertEqual(harness.engine.cancel_reasons, [])

        self.app.processEvents()
        event_names = [event["event"] for event in harness.bus.event_posted.emitted]
        self.assertIn("manual_stop_requested", event_names)

    def test_bottom_status_bar_uses_generation_session_bus_and_engine(self):
        harness = _BottomStatusBarHarness()

        with patch.object(ai_generation_module, "StatusBarWidget", _StatusBarStub, create=True):
            status_bar = harness._create_bottom_status_bar()

        self.assertIs(status_bar.parent, harness)
        self.assertIs(status_bar.event_bus, harness.bus)
        self.assertIs(status_bar.engine, harness.engine)

    def test_initial_settings_restore_saved_instances_after_loading_active_keys(self):
        harness = _GenerationSettingsHarness()

        harness._apply_initial_settings(
            {
                "provider": "gemini",
                "api_keys": ["key-1", "key-2", "key-3"],
                "num_instances": 3,
            }
        )

        self.assertEqual(harness.instances_spin.maximum(), 3)
        self.assertEqual(harness.instances_spin.value(), 3)
        self.assertEqual(harness.key_widget.get_active_keys(), ["key-1", "key-2", "key-3"])

    def test_initial_settings_restore_glossary_generation_controls(self):
        harness = _GenerationSettingsHarness()

        harness._apply_initial_settings(
            {
                "provider": "gemini",
                "api_keys": ["key-1", "key-2"],
                "is_sequential": True,
                "send_notes_in_sequence": False,
                "merge_mode": "accumulate",
                "num_instances": 2,
                "new_terms_limit": 123,
            }
        )

        self.assertTrue(harness.sequential_mode_checkbox.isChecked())
        self.assertFalse(harness.send_notes_checkbox.isChecked())
        self.assertTrue(harness.ai_mode_accumulate_radio.isChecked())
        self.assertEqual(harness.instances_spin.value(), 2)
        self.assertEqual(harness.new_terms_limit_spin.value(), 123)
        self.assertEqual(harness._pending_new_terms_limit, 123)

    def test_initial_settings_restore_shared_sleep_prevention_setting(self):
        harness = _GenerationSettingsHarness()

        harness._apply_initial_settings({"prevent_sleep_during_translation": True})

        self.assertTrue(harness.prevent_sleep_checkbox.isChecked())

    def test_saved_glossary_generation_settings_override_parent_defaults(self):
        harness = _GenerationSettingsHarness()
        harness.settings_manager = _SettingsManagerStub(
            {
                "is_sequential": True,
                "merge_mode": "update",
                "new_terms_limit": 77,
            }
        )

        merged = harness._merge_initial_ui_settings(
            {
                "is_sequential": False,
                "merge_mode": "supplement",
                "new_terms_limit": 50,
                "provider": "gemini",
            }
        )

        self.assertTrue(merged["is_sequential"])
        self.assertEqual(merged["merge_mode"], "update")
        self.assertEqual(merged["new_terms_limit"], 77)
        self.assertEqual(merged["provider"], "gemini")

    def test_shared_sleep_prevention_setting_overrides_stale_glossary_setting(self):
        harness = _GenerationSettingsHarness()
        harness.settings_manager = _SettingsManagerStub(
            {"prevent_sleep_during_translation": False}
        )
        harness.settings_manager.full_session = {
            "prevent_sleep_during_translation": True,
        }

        merged = harness._merge_initial_ui_settings({})

        self.assertTrue(merged["prevent_sleep_during_translation"])

    def test_ai_glossary_saves_sleep_prevention_to_shared_session_settings(self):
        harness = _GenerationSettingsHarness()
        harness.settings_manager.full_session = {"model": "kept-model"}

        harness._save_shared_sleep_prevention_setting(True)

        self.assertEqual(harness.settings_manager.saved_full_session["model"], "kept-model")
        self.assertTrue(
            harness.settings_manager.saved_full_session["prevent_sleep_during_translation"]
        )

    def test_persistent_settings_save_current_glossary_generation_state(self):
        harness = _GenerationSettingsHarness()
        harness.sequential_mode_checkbox.setChecked(True)
        harness.send_notes_checkbox.setChecked(False)
        harness.ai_mode_update_radio.setChecked(True)
        harness.instances_spin.setMaximum(4)
        harness.instances_spin.setValue(3)
        harness.new_terms_limit_spin.setValue(88)
        harness._mark_new_terms_limit_user_defined()

        harness._save_persistent_ui_settings()

        self.assertEqual(harness.settings_manager.persisted["merge_mode"], "update")
        self.assertTrue(harness.settings_manager.persisted["is_sequential"])
        self.assertFalse(harness.settings_manager.persisted["send_notes_in_sequence"])
        self.assertEqual(harness.settings_manager.persisted["num_instances"], 3)
        self.assertEqual(harness.settings_manager.persisted["new_terms_limit"], 88)
        self.assertTrue(harness.settings_manager.persisted["new_terms_limit_user_defined"])

    def test_glossary_generation_common_settings_include_shared_sleep_prevention(self):
        harness = _GenerationSettingsHarness()
        harness.prevent_sleep_checkbox.setChecked(True)

        settings = harness._get_common_settings()

        self.assertTrue(settings["prevent_sleep_during_translation"])

    def test_auto_new_terms_limit_has_practical_floor(self):
        harness = _GenerationSettingsHarness()
        harness.translation_options_widget = _TranslationOptionsForBatchSizeStub(value=20000)

        harness._update_new_terms_limit_from_current_size()

        self.assertEqual(harness.new_terms_limit_spin.value(), 100)
        self.assertFalse(harness._new_terms_limit_user_defined)

    def test_manual_new_terms_limit_is_not_overwritten_by_auto_recalc(self):
        harness = _GenerationSettingsHarness()
        harness.translation_options_widget = _TranslationOptionsForBatchSizeStub(value=30000)
        harness._apply_new_terms_limit_value(100, user_defined=True)

        harness._update_new_terms_limit_from_current_size()

        self.assertEqual(harness.new_terms_limit_spin.value(), 100)
        self.assertTrue(harness._new_terms_limit_user_defined)

    def test_legacy_auto_new_terms_limit_below_default_is_recomputed(self):
        harness = _GenerationSettingsHarness()
        harness.translation_options_widget = _TranslationOptionsForBatchSizeStub(value=20000)
        user_defined = harness._new_terms_limit_user_defined_from_settings({"new_terms_limit": 40})
        harness._apply_new_terms_limit_value(40, user_defined=user_defined)

        harness._update_new_terms_limit_from_current_size()

        self.assertEqual(harness.new_terms_limit_spin.value(), 100)
        self.assertFalse(harness._new_terms_limit_user_defined)

    def test_initial_settings_do_not_treat_inherited_task_size_as_glossary_user_size(self):
        harness = _GenerationSettingsHarness()

        harness._apply_initial_settings(
            {
                "task_size_limit": 15404,
                "task_size_limit_user_defined": True,
            }
        )

        self.assertFalse(harness.translation_options_widget.received_settings["task_size_limit_user_defined"])

    def test_optimal_batch_size_does_not_replace_glossary_user_task_size(self):
        harness = _GlossaryBatchSizeHarness(user_defined=True)

        with patch.object(api_config, "all_models", return_value={"test-model": {"context_length": 200000}}):
            harness._calculate_optimal_batch_size()

        self.assertEqual(harness.translation_options_widget.task_size_spin.value(), 15404)
        self.assertEqual(harness.translation_options_widget.info_updates, 1)

    def test_optimal_batch_size_updates_auto_task_size(self):
        harness = _GlossaryBatchSizeHarness(user_defined=False)

        with patch.object(api_config, "all_models", return_value={"test-model": {"context_length": 230000}}):
            harness._calculate_optimal_batch_size()

        self.assertEqual(harness.translation_options_widget.task_size_spin.value(), 69000)
        self.assertIn("симв.", harness.translation_options_widget.info_label.text)
        self.assertEqual(harness.translation_options_widget.info_updates, 0)


if __name__ == "__main__":
    unittest.main()
