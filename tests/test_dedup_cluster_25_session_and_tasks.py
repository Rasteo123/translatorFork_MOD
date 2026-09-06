"""cluster-25: устранение дублирования блока управления очередью/сессией
между setup.py (InitialSetupPage) и ai_generation.py (GenerationSessionPage).

Структура файла:
1. Характеризационные тесты на каноническую реализацию в
   gemini_translator.ui.dialogs._shared.session_and_tasks (крайние случаи,
   которые различали копии — прежде всего учёт browser_profiles_count и
   поддержка split_batch/reorder_batch_chapters).
2. Тесты-маршрутизация: подменяют каноническую функцию и проверяют, что
   оба бывших места копирования (setup.py и ai_generation.py) идут через
   неё, а не через собственную копию тела.
"""

import os
import types
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gemini_translator.ui.dialogs._shared import session_and_tasks
from gemini_translator.ui.dialogs import setup as setup_dialog_module
from gemini_translator.ui.dialogs.glossary_dialogs import ai_generation as ai_generation_module


# ---------------------------------------------------------------------------
# 1. Характеризационные тесты канонической реализации
# ---------------------------------------------------------------------------

class _FakeKeyWidget:
    def __init__(self, provider_id, active_keys, can_start=False, has_can_start_attr=True):
        self._provider_id = provider_id
        self._active_keys = list(active_keys)
        if has_can_start_attr:
            self.can_start_ai_session = lambda: can_start

    def get_selected_provider(self):
        return self._provider_id

    def get_active_keys(self):
        return self._active_keys


class _FakeModelSettingsWidget:
    def __init__(self, settings):
        self._settings = dict(settings)

    def get_settings(self):
        return dict(self._settings)


class SessionCapacityCharacterizationTests(unittest.TestCase):
    def _patch_api_config(self, *, requires_api_key=True, legacy_worker_thread=False, max_instances=None):
        patchers = [
            patch.object(session_and_tasks.api_config, "api_providers", return_value={"p": {}}),
            patch.object(session_and_tasks.api_config, "provider_requires_api_key", return_value=requires_api_key),
            patch.object(session_and_tasks.api_config, "uses_legacy_worker_thread", return_value=legacy_worker_thread),
            patch.object(session_and_tasks.api_config, "provider_max_instances", return_value=max_instances),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)

    def test_no_active_sessions_but_can_start_returns_one(self):
        self._patch_api_config()
        key_widget = _FakeKeyWidget("p", [], can_start=True)
        self.assertEqual(session_and_tasks.get_available_session_capacity(key_widget), 1)

    def test_no_active_sessions_and_cannot_start_returns_zero(self):
        self._patch_api_config()
        key_widget = _FakeKeyWidget("p", [], can_start=False)
        self.assertEqual(session_and_tasks.get_available_session_capacity(key_widget), 0)

    def test_no_active_sessions_without_can_start_attr_returns_zero(self):
        self._patch_api_config()
        key_widget = _FakeKeyWidget("p", [], has_can_start_attr=False)
        self.assertEqual(session_and_tasks.get_available_session_capacity(key_widget), 0)

    def test_normal_path_min_of_active_and_provider_limit(self):
        self._patch_api_config(max_instances=2)
        key_widget = _FakeKeyWidget("p", ["k1", "k2", "k3"])
        self.assertEqual(session_and_tasks.get_available_session_capacity(key_widget), 2)

    def test_provider_limit_none_falls_back_to_active_sessions(self):
        self._patch_api_config(max_instances=None)
        key_widget = _FakeKeyWidget("p", ["k1", "k2"])
        self.assertEqual(session_and_tasks.get_available_session_capacity(key_widget), 2)

    def test_legacy_worker_thread_with_profile_count_used_when_widget_present(self):
        # Развилка, из-за которой копии разошлись: провайдер без API-ключа на
        # legacy worker thread должен учитывать browser_profiles_count, если
        # model_settings_widget передан диалогом.
        self._patch_api_config(requires_api_key=False, legacy_worker_thread=True, max_instances=1)
        key_widget = _FakeKeyWidget("p", ["k1"])
        model_settings_widget = _FakeModelSettingsWidget({"browser_profiles_count": 4})
        self.assertEqual(
            session_and_tasks.get_available_session_capacity(key_widget, model_settings_widget),
            4,
        )

    def test_legacy_worker_thread_ignored_without_model_settings_widget(self):
        self._patch_api_config(requires_api_key=False, legacy_worker_thread=True, max_instances=1)
        key_widget = _FakeKeyWidget("p", ["k1"])
        # model_settings_widget не передан (диалог без такого виджета) —
        # ветка browser_profiles_count не должна применяться.
        self.assertEqual(session_and_tasks.get_available_session_capacity(key_widget), 1)

    def test_legacy_worker_thread_profile_count_one_falls_through(self):
        self._patch_api_config(requires_api_key=False, legacy_worker_thread=True, max_instances=1)
        key_widget = _FakeKeyWidget("p", ["k1"])
        model_settings_widget = _FakeModelSettingsWidget({"browser_profiles_count": 1})
        self.assertEqual(
            session_and_tasks.get_available_session_capacity(key_widget, model_settings_widget),
            1,
        )

    def test_provider_requires_api_key_ignores_profile_count(self):
        self._patch_api_config(requires_api_key=True, legacy_worker_thread=True, max_instances=1)
        key_widget = _FakeKeyWidget("p", ["k1"])
        model_settings_widget = _FakeModelSettingsWidget({"browser_profiles_count": 4})
        self.assertEqual(
            session_and_tasks.get_available_session_capacity(key_widget, model_settings_widget),
            1,
        )

    def test_bad_profile_count_value_defaults_to_one(self):
        self._patch_api_config(requires_api_key=False, legacy_worker_thread=True, max_instances=1)
        key_widget = _FakeKeyWidget("p", ["k1"])
        model_settings_widget = _FakeModelSettingsWidget({"browser_profiles_count": "oops"})
        self.assertEqual(
            session_and_tasks.get_available_session_capacity(key_widget, model_settings_widget),
            1,
        )


class TaskActionDispatchCharacterizationTests(unittest.TestCase):
    def test_reorder_actions(self):
        task_manager = types.SimpleNamespace(reorder_tasks=MagicMock())
        for action in ("top", "bottom", "up", "down"):
            method, args = session_and_tasks.resolve_task_action(task_manager, action, [1, 2])
            self.assertIs(method, task_manager.reorder_tasks)
            self.assertEqual(args, [action, [1, 2]])

    def test_remove(self):
        task_manager = types.SimpleNamespace(remove_tasks=MagicMock())
        method, args = session_and_tasks.resolve_task_action(task_manager, "remove", [1])
        self.assertIs(method, task_manager.remove_tasks)
        self.assertEqual(args, [[1]])

    def test_duplicate(self):
        task_manager = types.SimpleNamespace(duplicate_tasks=MagicMock())
        method, args = session_and_tasks.resolve_task_action(task_manager, "duplicate", [1])
        self.assertIs(method, task_manager.duplicate_tasks)
        self.assertEqual(args, [[1]])

    def test_split_batch_supported_when_flag_enabled(self):
        task_manager = types.SimpleNamespace(split_batches_into_chapters=MagicMock())
        method, args = session_and_tasks.resolve_task_action(
            task_manager, "split_batch", [1], support_batch_split=True
        )
        self.assertIs(method, task_manager.split_batches_into_chapters)
        self.assertEqual(args, [[1]])

    def test_split_batch_unsupported_when_flag_disabled(self):
        task_manager = types.SimpleNamespace(split_batches_into_chapters=MagicMock())
        method, args = session_and_tasks.resolve_task_action(
            task_manager, "split_batch", [1], support_batch_split=False
        )
        self.assertIsNone(method)

    def test_reorder_batch_chapters_supported_when_flag_enabled(self):
        task_manager = types.SimpleNamespace(reorder_batch_chapters=MagicMock())
        method, args = session_and_tasks.resolve_task_action(
            task_manager, "reorder_batch_chapters", ("task1", [3, 1, 2]), support_batch_split=True
        )
        self.assertIs(method, task_manager.reorder_batch_chapters)
        self.assertEqual(args, ["task1", [3, 1, 2]])

    def test_reorder_batch_chapters_unsupported_when_flag_disabled(self):
        task_manager = types.SimpleNamespace(reorder_batch_chapters=MagicMock())
        method, args = session_and_tasks.resolve_task_action(
            task_manager, "reorder_batch_chapters", ("task1", [3, 1, 2]), support_batch_split=False
        )
        self.assertIsNone(method)

    def test_unknown_action_returns_none(self):
        task_manager = types.SimpleNamespace()
        method, args = session_and_tasks.resolve_task_action(task_manager, "nonsense", [1])
        self.assertIsNone(method)

    def test_status_message_variants(self):
        self.assertEqual(
            session_and_tasks.task_action_status_message("split_batch"),
            "Разбиваю пакеты на главы...",
        )
        self.assertEqual(
            session_and_tasks.task_action_status_message("reorder_batch_chapters"),
            "Сохраняю порядок глав в пакете...",
        )
        self.assertEqual(
            session_and_tasks.task_action_status_message("remove"),
            "Обновление списка задач...",
        )


# ---------------------------------------------------------------------------
# 2. Тесты-маршрутизация: setup.py и ai_generation.py обязаны звать
#    каноническую реализацию, а не собственную копию.
# ---------------------------------------------------------------------------

class _FakeSignal:
    def __init__(self):
        self.slot = None

    def connect(self, slot):
        self.slot = slot


class _FakeTaskDBWorker:
    """Заглушка вместо TaskDBWorker(QThread) — не запускает настоящий поток."""

    instances = []

    def __init__(self, target_func, *args, **kwargs):
        self.target_func = target_func
        self.args = args
        self.kwargs = kwargs
        self.finished = _FakeSignal()
        self.started = False
        _FakeTaskDBWorker.instances.append(self)

    def start(self):
        self.started = True


class SetupRoutingTests(unittest.TestCase):
    """setup.py: InitialSetupPage должна маршрутизировать через session_and_tasks."""

    def setUp(self):
        _FakeTaskDBWorker.instances = []

    def test_get_available_session_capacity_routes_through_shared_module(self):
        sentinel = 4242
        recorded = {}

        def fake_get_capacity(key_widget, model_settings_widget=None):
            recorded["key_widget"] = key_widget
            recorded["model_settings_widget"] = model_settings_widget
            return sentinel

        stub = types.SimpleNamespace(key_management_widget=object())

        with patch.object(setup_dialog_module.session_and_tasks, "get_available_session_capacity", fake_get_capacity):
            result = setup_dialog_module.InitialSetupPage._get_available_session_capacity(stub)

        self.assertEqual(result, sentinel)
        self.assertIs(recorded["key_widget"], stub.key_management_widget)

    def test_emit_task_manipulation_signal_routes_through_shared_dispatch_with_batch_support(self):
        recorded_resolve_calls = []
        fake_method = MagicMock()

        def fake_resolve(task_manager, action, payload, support_batch_split=False):
            recorded_resolve_calls.append((task_manager, action, payload, support_batch_split))
            return fake_method, [payload]

        task_manager = object()
        stub = types.SimpleNamespace(
            engine=types.SimpleNamespace(task_manager=task_manager),
            task_management_widget=MagicMock(),
            status_bar=MagicMock(),
            db_worker=None,
            _on_db_worker_finished=lambda: None,
        )

        with patch.object(setup_dialog_module.session_and_tasks, "resolve_task_action", fake_resolve), \
             patch.object(setup_dialog_module.session_and_tasks, "task_action_status_message", return_value="msg"), \
             patch.object(setup_dialog_module, "TaskDBWorker", _FakeTaskDBWorker):
            setup_dialog_module.InitialSetupPage._emit_task_manipulation_signal(stub, "split_batch", [1, 2])

        # Диалог с task_management_widget (поддерживает батчи) обязан просить
        # диспетчер поддержать split_batch/reorder_batch_chapters.
        self.assertEqual(
            recorded_resolve_calls,
            [(task_manager, "split_batch", [1, 2], True)],
        )
        stub.task_management_widget.setEnabled.assert_called_once_with(False)
        self.assertEqual(len(_FakeTaskDBWorker.instances), 1)
        self.assertTrue(_FakeTaskDBWorker.instances[0].started)


class AiGenerationRoutingTests(unittest.TestCase):
    """ai_generation.py: GenerationSessionPage должна маршрутизировать через session_and_tasks."""

    def setUp(self):
        _FakeTaskDBWorker.instances = []

    def test_get_available_session_capacity_routes_through_shared_module(self):
        sentinel = 99
        recorded = {}

        def fake_get_capacity(key_widget, model_settings_widget=None):
            recorded["key_widget"] = key_widget
            recorded["model_settings_widget"] = model_settings_widget
            return sentinel

        stub = types.SimpleNamespace(key_widget=object())

        with patch.object(
            ai_generation_module.session_and_tasks, "get_available_session_capacity", fake_get_capacity
        ):
            result = ai_generation_module.GenerationSessionPage._get_available_session_capacity(stub)

        self.assertEqual(result, sentinel)
        self.assertIs(recorded["key_widget"], stub.key_widget)

    def test_emit_task_manipulation_signal_routes_through_shared_dispatch_without_batch_support(self):
        recorded_resolve_calls = []
        fake_method = MagicMock()

        def fake_resolve(task_manager, action, payload, support_batch_split=False):
            recorded_resolve_calls.append((task_manager, action, payload, support_batch_split))
            return fake_method, [payload]

        task_manager = object()
        stub = types.SimpleNamespace(
            engine=types.SimpleNamespace(task_manager=task_manager),
            task_manager=task_manager,
            chapter_list_widget=MagicMock(),
            rebuild_tasks_btn=MagicMock(),
            db_worker=None,
            _on_db_worker_finished=lambda: None,
        )

        with patch.object(ai_generation_module.session_and_tasks, "resolve_task_action", fake_resolve), \
             patch.object(ai_generation_module, "TaskDBWorker", _FakeTaskDBWorker):
            ai_generation_module.GenerationSessionPage._emit_task_manipulation_signal(stub, "duplicate", [7])

        # Диалог без task_management_widget (chapter_list_widget) НЕ должен
        # получать поддержку split_batch/reorder_batch_chapters — это
        # сохраняет прежнее (уже разошедшееся) поведение копии.
        self.assertEqual(
            recorded_resolve_calls,
            [(task_manager, "duplicate", [7], False)],
        )
        stub.chapter_list_widget.setEnabled.assert_called_once_with(False)
        stub.rebuild_tasks_btn.setEnabled.assert_called_once_with(False)
        self.assertEqual(len(_FakeTaskDBWorker.instances), 1)
        self.assertTrue(_FakeTaskDBWorker.instances[0].started)


if __name__ == "__main__":
    unittest.main()
