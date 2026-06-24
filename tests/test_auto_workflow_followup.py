import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from PyQt6 import QtWidgets

from gemini_translator.core.consistency_engine import (
    FAST_PROOFREAD_MODE,
    filter_consistency_problems_by_confidence,
)
from gemini_translator.ui.dialogs.auto_workflow import (
    AutoConsistencyWorker,
    load_project_chapters_for_consistency,
)
from gemini_translator.ui.dialogs.setup import InitialSetupDialog
from gemini_translator.ui.widgets.auto_translate_widget import AutoTranslateWidget


class _AutoTranslateWidgetStub:
    def __init__(self, settings):
        self._settings = dict(settings)

    def get_settings(self):
        return dict(self._settings)


class _AutoTranslateSettingsManagerStub:
    def load_glossary_prompts(self):
        return {}

    def get_last_glossary_prompt_text(self):
        return ""

    def get_last_auto_translation_settings(self):
        return {}

    def get_last_auto_translation_preset_name(self):
        return None

    def load_auto_translation_presets(self):
        return {}

    def save_auto_translation_presets(self, presets):
        return True

    def save_last_auto_translation_settings(self, settings):
        self.last_auto_translation_settings = dict(settings)

    def save_last_auto_translation_preset_name(self, name):
        self.last_auto_translation_preset_name = name


class _KeyManagementWidgetStub:
    def get_selected_provider(self):
        return "stub-provider"

    def get_active_keys(self):
        return ["stub-key"]


class _ValidatorDialogStub:
    def __init__(
        self,
        results_data,
        auto_fix_result=None,
        allow_auto_fix=True,
        request_details_text="",
    ):
        self.results_data = results_data
        self.auto_fix_result = auto_fix_result
        self.allow_auto_fix = allow_auto_fix
        self.request_details_text = request_details_text
        self.auto_fix_calls = 0
        self.deleted = False

    def run_auto_untranslated_fixer(self, **kwargs):
        self.auto_fix_calls += 1
        if not self.allow_auto_fix:
            raise AssertionError("run_auto_untranslated_fixer should not be called")
        return dict(self.auto_fix_result or {})

    def build_auto_untranslated_request_details(self, **kwargs):
        return self.request_details_text

    def deleteLater(self):
        self.deleted = True


class _CheckStateStub:
    def __init__(self):
        self.checked = False
        self.values = []

    def setChecked(self, value):
        self.checked = bool(value)
        self.values.append(self.checked)

    def isChecked(self):
        return self.checked


class _ConnectOnlySignal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)


class _AutoWorkflowHarness:
    _on_auto_validator_finished = InitialSetupDialog._on_auto_validator_finished
    _on_auto_consistency_finished = InitialSetupDialog._on_auto_consistency_finished
    _finish_auto_validator_followup = InitialSetupDialog._finish_auto_validator_followup
    _compose_auto_details = InitialSetupDialog._compose_auto_details
    _compose_auto_trace_details = InitialSetupDialog._compose_auto_trace_details

    def __init__(self, dialog, settings, repeated_signatures=None):
        self._auto_validator_dialog = dialog
        self.auto_translate_widget = _AutoTranslateWidgetStub(settings)
        self.key_management_widget = _KeyManagementWidgetStub()
        self.selected_file = "C:/project/book.epub"
        self._auto_followup_running = True
        self._auto_last_retry_signatures = set()
        self._auto_last_untranslated_fix_signatures = set(repeated_signatures or set())
        self.logs = []
        self.consistency_calls = []
        self.retry_requests = []
        self.reset_calls = 0
        self.ready_calls = 0
        self.project_manager = None

    def _get_effective_auto_short_ratio_limit(self, auto_settings, data):
        return 0.70, "default"

    def _get_effective_auto_model_settings(self, auto_settings):
        return {}

    def _auto_log(self, message, force=False, **kwargs):
        entry = {"message": message, "force": force}
        entry.update(kwargs)
        self.logs.append(entry)

    def _format_auto_chapter_list(self, chapters, limit=10, preserve_order=False):
        values = list(chapters[:limit] if isinstance(chapters, tuple) else list(chapters)[:limit])
        return ", ".join(values)

    def _short_auto_name(self, path):
        return os.path.basename(path)

    @staticmethod
    def _truncate_auto_trace_text(text, limit=4000):
        return InitialSetupDialog._truncate_auto_trace_text(text, limit)

    @staticmethod
    def _merge_auto_details(*parts):
        return InitialSetupDialog._merge_auto_details(*parts)

    def _run_auto_consistency_followup(self, auto_settings):
        self.consistency_calls.append(dict(auto_settings))

    def add_files_for_retry(self, epub_path, chapter_paths):
        self.retry_requests.append((epub_path, list(chapter_paths)))

    def _reset_auto_workflow_state(self):
        self.reset_calls += 1

    def check_ready(self):
        self.ready_calls += 1


class _AutoRatioHarness:
    _get_effective_auto_short_ratio_limit = InitialSetupDialog._get_effective_auto_short_ratio_limit
    _auto_result_uses_cjk_ratio = InitialSetupDialog._auto_result_uses_cjk_ratio
    _auto_original_chapter_has_cjk = InitialSetupDialog._auto_original_chapter_has_cjk

    def __init__(self, selected_file=None):
        self.selected_file = selected_file


class _AutoValidatorDialogLaunchStub:
    instances = []

    def __init__(
        self,
        translated_folder,
        original_epub_path,
        parent=None,
        retry_enabled=True,
        project_manager=None,
    ):
        self.translated_folder = translated_folder
        self.original_epub_path = original_epub_path
        self.parent = parent
        self.retry_enabled = retry_enabled
        self.project_manager = project_manager
        self.check_show_all = _CheckStateStub()
        self.check_revalidate_ok = _CheckStateStub()
        self.path_row_map = {"Text/ch1.xhtml": 0}
        self.start_analysis_targets = []
        self.analysis_thread = type(
            "_AnalysisThreadStub",
            (),
            {"analysis_finished": _ConnectOnlySignal()},
        )()
        self.hide_called = False
        self.start_analysis_calls = 0
        self.deleted = False
        self.__class__.instances.append(self)

    def hide(self):
        self.hide_called = True

    def start_analysis(self, specific_targets=None):
        self.start_analysis_calls += 1
        self.start_analysis_targets.append(
            tuple(specific_targets) if specific_targets is not None else None
        )

    def _get_eligible_analysis_paths(self):
        return set(self.path_row_map.keys())

    def deleteLater(self):
        self.deleted = True


class _EventLoopStub:
    def quit(self):
        return None

    def exec(self):
        return None


class _RunAutoValidatorHarness:
    _run_auto_validator_followup = InitialSetupDialog._run_auto_validator_followup

    def __init__(self):
        self.output_folder = "C:/project"
        self.selected_file = "C:/project/book.epub"
        self.project_manager = object()
        self.start_btn = _ButtonStub()
        self.logs = []
        self._auto_followup_running = False
        self._auto_validator_dialog = None
        self.reset_calls = 0
        self.ready_calls = 0
        self.finished_calls = []

    def _auto_log(self, message, force=False, **kwargs):
        entry = {"message": message, "force": force}
        entry.update(kwargs)
        self.logs.append(entry)

    def _reset_auto_workflow_state(self):
        self.reset_calls += 1

    def check_ready(self):
        self.ready_calls += 1

    def _finish_auto_validator_followup(self, auto_settings, log_message=None):
        self.finished_calls.append(
            {
                "auto_settings": dict(auto_settings),
                "log_message": log_message,
            }
        )

    def _on_auto_validator_finished(self, total_scanned, suspicious_found):
        self.finished_calls.append(
            {
                "total_scanned": total_scanned,
                "suspicious_found": suspicious_found,
            }
        )


class _StartTaskManagerStub:
    def __init__(self, has_pending_tasks):
        self._has_pending_tasks = bool(has_pending_tasks)
        self.release_calls = 0

    def has_pending_tasks(self):
        return self._has_pending_tasks

    def release_held_tasks(self):
        self.release_calls += 1


class _StartEngineStub:
    def __init__(self, has_pending_tasks):
        self.task_manager = _StartTaskManagerStub(has_pending_tasks)


class _StartKeyManagementWidgetStub:
    def __init__(self, active_keys):
        self._active_keys = list(active_keys or [])

    def get_active_keys(self):
        return list(self._active_keys)


class _StartTranslationHarness:
    _start_translation = InitialSetupDialog._start_translation

    def __init__(self, has_pending_tasks=True, active_keys=None):
        self.selected_file = "C:/project/book.epub"
        self.output_folder = "C:/project"
        self.engine = _StartEngineStub(has_pending_tasks)
        self.key_management_widget = _StartKeyManagementWidgetStub(active_keys)
        self.auto_translate_widget = _AutoTranslateWidgetStub({"enabled": True})
        self._auto_restart_session_override = None
        self._auto_followup_running = True
        self.logs = []
        self.reset_calls = 0
        self.ready_calls = 0

    def _check_and_sync_active_session(self):
        return False

    def _resolve_auto_translation_options(self, auto_settings):
        return {}, "inherit", False, 0, None, None

    def _resolve_auto_model_override(self, auto_settings):
        return None, None, None

    def _ensure_pending_tasks_for_start(self):
        task_manager = self.engine.task_manager if self.engine else None
        return bool(task_manager and task_manager.has_pending_tasks())

    def _auto_log(self, message, force=False, **kwargs):
        entry = {"message": message, "force": force}
        entry.update(kwargs)
        self.logs.append(entry)

    def _reset_auto_workflow_state(self):
        self.reset_calls += 1
        self._auto_followup_running = False

    def check_ready(self):
        self.ready_calls += 1


class _TranslationOptionsWidgetStub:
    def get_settings(self):
        return {
            "use_batching": False,
            "chunking": False,
            "chunk_on_error": False,
            "sequential_translation": True,
            "sequential_translation_splits": 1,
            "task_size_limit": 1500,
        }


class _AutoTranslationOptionsHarness:
    _resolve_auto_translation_options = InitialSetupDialog._resolve_auto_translation_options

    def __init__(self):
        self.translation_options_widget = _TranslationOptionsWidgetStub()

    def _estimate_auto_task_size_limit(self, token_limit: int):
        return int(token_limit) * 2, "stub-profile"


class _ConsistencyProjectManagerStub:
    def __init__(self, project_folder, originals, versions_map):
        self.project_folder = project_folder
        self._originals = list(originals)
        self._versions_map = {
            original: dict(versions)
            for original, versions in versions_map.items()
        }

    def get_all_originals(self):
        return list(self._originals)

    def get_versions_for_original(self, original_path):
        return dict(self._versions_map.get(original_path, {}))


class AutoWorkflowFollowupTests(unittest.TestCase):
    def test_auto_translation_options_include_chapter_limit(self):
        harness = _AutoTranslationOptionsHarness()

        options, mode, has_override, batch_tokens, batch_task_limit, profile = (
            harness._resolve_auto_translation_options({
                "translation_mode_override": "batch",
                "batch_token_limit_override": 2000,
                "batch_chapter_limit_override": 3,
            })
        )

        self.assertEqual(mode, "batch")
        self.assertTrue(has_override)
        self.assertEqual(batch_tokens, 2000)
        self.assertEqual(batch_task_limit, 4000)
        self.assertEqual(profile, "stub-profile")
        self.assertTrue(options["use_batching"])
        self.assertFalse(options["chunking"])
        self.assertEqual(options["max_chapters_per_batch"], 3)
        self.assertNotIn("sequential_original_context_enabled", options)
        self.assertNotIn("sequential_original_context_chapters", options)
        self.assertNotIn("sequential_original_context_char_limit", options)

    def test_load_project_chapters_for_consistency_can_include_original_epub_text(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = os.path.join(temp_dir, "book.epub")
            translated_rel_path = os.path.join("Text", "chapter1.html")
            translated_full_path = os.path.join(temp_dir, translated_rel_path)
            os.makedirs(os.path.dirname(translated_full_path), exist_ok=True)
            with open(translated_full_path, "w", encoding="utf-8") as handle:
                handle.write("<html><body><p>Он вошёл в зал.</p></body></html>")

            internal_path = "Text/chapter1.xhtml"
            with zipfile.ZipFile(epub_path, "w") as epub_zip:
                epub_zip.writestr(internal_path, "<html><body><p>他走进大厅。</p></body></html>")

            project_manager = _ConsistencyProjectManagerStub(
                temp_dir,
                [internal_path],
                {internal_path: {"": translated_rel_path}},
            )

            chapters = load_project_chapters_for_consistency(
                project_manager,
                original_epub_path=epub_path,
                include_original=True,
            )

            self.assertEqual(len(chapters), 1)
            self.assertEqual(chapters[0]["name"], "chapter1.xhtml")
            self.assertIn("Он вошёл", chapters[0]["content"])
            self.assertIn("他走进大厅", chapters[0]["source_content"])
            self.assertEqual(chapters[0]["source_path"], internal_path)

    def test_load_project_chapters_for_consistency_omits_original_without_flag(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = os.path.join(temp_dir, "book.epub")
            translated_rel_path = os.path.join("Text", "chapter1.html")
            translated_full_path = os.path.join(temp_dir, translated_rel_path)
            os.makedirs(os.path.dirname(translated_full_path), exist_ok=True)
            with open(translated_full_path, "w", encoding="utf-8") as handle:
                handle.write("<p>Перевод</p>")

            internal_path = "Text/chapter1.xhtml"
            with zipfile.ZipFile(epub_path, "w") as epub_zip:
                epub_zip.writestr(internal_path, "<p>原文</p>")

            project_manager = _ConsistencyProjectManagerStub(
                temp_dir,
                [internal_path],
                {internal_path: {"": translated_rel_path}},
            )

            chapters = load_project_chapters_for_consistency(
                project_manager,
                original_epub_path=epub_path,
                include_original=False,
            )

            self.assertEqual(len(chapters), 1)
            self.assertNotIn("source_content", chapters[0])

    def test_auto_short_ratio_uses_cjk_limit_from_flag(self):
        harness = _AutoRatioHarness()

        ratio_limit, profile = harness._get_effective_auto_short_ratio_limit(
            {"retry_short_ratio": 0.70},
            {"is_cjk_original": True},
        )

        self.assertEqual(ratio_limit, 1.80)
        self.assertEqual(profile, "CJK")

    def test_auto_short_ratio_detects_cjk_original_html_without_cached_flag(self):
        harness = _AutoRatioHarness()

        ratio_limit, profile = harness._get_effective_auto_short_ratio_limit(
            {"retry_short_ratio": 0.70},
            {"original_html": "<html><body><p>她抬头看向窗外。</p></body></html>"},
        )

        self.assertEqual(ratio_limit, 1.80)
        self.assertEqual(profile, "CJK")

    def test_auto_short_ratio_detects_cjk_original_from_epub(self):
        with tempfile.NamedTemporaryFile(suffix=".epub", delete=False) as temp_file:
            epub_path = temp_file.name

        try:
            internal_path = "Text/chapter1.xhtml"
            with zipfile.ZipFile(epub_path, "w") as epub_zip:
                epub_zip.writestr(internal_path, "<html><body><p>彼は静かに頷いた。</p></body></html>")

            harness = _AutoRatioHarness(selected_file=epub_path)

            ratio_limit, profile = harness._get_effective_auto_short_ratio_limit(
                {"retry_short_ratio": 0.70},
                {"internal_html_path": internal_path},
            )

            self.assertEqual(ratio_limit, 1.80)
            self.assertEqual(profile, "CJK")
        finally:
            os.remove(epub_path)

    def test_auto_short_ratio_keeps_user_limit_for_alphabetic_original(self):
        harness = _AutoRatioHarness()

        ratio_limit, profile = harness._get_effective_auto_short_ratio_limit(
            {"retry_short_ratio": 0.70},
            {"original_html": "<html><body><p>She looked out the window.</p></body></html>"},
        )

        self.assertEqual(ratio_limit, 0.70)
        self.assertEqual(profile, "alphabetic")

    def test_repeated_short_retry_signature_is_requeued(self):
        settings = {
            "retry_short_enabled": True,
            "retry_untranslated_enabled": False,
            "auto_restart_after_retry": False,
        }
        short_chapter = "Text/ch1.xhtml"
        dialog = _ValidatorDialogStub(
            {
                0: {
                    "internal_html_path": short_chapter,
                    "len_orig": 500,
                    "ratio_value": 0.20,
                }
            },
            allow_auto_fix=False,
        )
        harness = _AutoWorkflowHarness(dialog, settings)
        harness._auto_last_retry_signatures.add((short_chapter,))

        harness._on_auto_validator_finished(total_scanned=1, suspicious_found=1)

        self.assertEqual(harness.retry_requests, [(harness.selected_file, [short_chapter])])
        self.assertEqual(harness.reset_calls, 1)
        self.assertEqual(harness.ready_calls, 1)

    def test_repeated_untranslated_signature_advances_to_consistency(self):
        settings = {
            "retry_short_enabled": False,
            "retry_untranslated_enabled": True,
            "ai_consistency_enabled": True,
        }
        dialog = _ValidatorDialogStub(
            {
                0: {
                    "internal_html_path": "Text/ch1.xhtml",
                    "untranslated_words": ["hero"],
                }
            },
            allow_auto_fix=False,
            request_details_text=(
                "Промпт:\nTranslate only the untranslated fragments.\n\n"
                "Запрос 1:\n<html><body><p data-id=\"0\">hero</p></body></html>"
            ),
        )
        harness = _AutoWorkflowHarness(
            dialog,
            settings,
            repeated_signatures={("Text/ch1.xhtml",)},
        )

        harness._on_auto_validator_finished(total_scanned=1, suspicious_found=1)

        self.assertTrue(dialog.deleted)
        self.assertIsNone(harness._auto_validator_dialog)
        self.assertEqual(dialog.auto_fix_calls, 0)
        self.assertEqual(len(harness.consistency_calls), 1)
        self.assertEqual(harness.reset_calls, 0)
        self.assertEqual(harness.ready_calls, 0)
        self.assertTrue(
            any("Продолжаю автопайплайн без повторного точечного фикса недоперевода." in entry["message"] for entry in harness.logs)
        )
        self.assertTrue(
            any(
                entry["message"].startswith("Недоперевод найден")
                and "details_text" in entry
                and "Translate only the untranslated fragments." in entry["details_text"]
                for entry in harness.logs
            )
        )

    def test_failed_untranslated_fix_still_advances_to_consistency(self):
        settings = {
            "retry_short_enabled": False,
            "retry_untranslated_enabled": True,
            "ai_consistency_enabled": True,
        }
        dialog = _ValidatorDialogStub(
            {
                0: {
                    "internal_html_path": "Text/ch2.xhtml",
                    "untranslated_words": ["villain"],
                }
            },
            auto_fix_result={
                "success": False,
                "error": "boom",
                "request_details_text": (
                    "Промпт:\nTranslate only the untranslated fragments.\n\n"
                    "Запрос 1:\n<html><body><p data-id=\"0\">villain</p></body></html>"
                ),
                "response_details_text": (
                    "Промпт:\nTranslate only the untranslated fragments.\n\n"
                    "Ответ 1:\n<html><body><p data-id=\"0\">villain fixed</p></body></html>"
                ),
            },
            allow_auto_fix=True,
            request_details_text=(
                "Промпт:\nTranslate only the untranslated fragments.\n\n"
                "Запрос 1:\n<html><body><p data-id=\"0\">villain</p></body></html>"
            ),
        )
        harness = _AutoWorkflowHarness(dialog, settings)

        harness._on_auto_validator_finished(total_scanned=1, suspicious_found=1)

        self.assertTrue(dialog.deleted)
        self.assertEqual(dialog.auto_fix_calls, 1)
        self.assertEqual(len(harness.consistency_calls), 1)
        self.assertEqual(harness.reset_calls, 0)
        self.assertEqual(harness.ready_calls, 0)
        self.assertTrue(
            any("Продолжаю автопайплайн без точечного фикса недоперевода." in entry["message"] for entry in harness.logs)
        )
        self.assertTrue(
            any(
                entry["message"].startswith("Запускаю точечное исправление недоперевода")
                and entry.get("details_title") == "[AUTO] Точечный фикс недоперевода"
                and "Translate only the untranslated fragments." in entry.get("details_text", "")
                for entry in harness.logs
            )
        )

        self.assertTrue(
            any(
                "details_text" in entry
                and "villain fixed" in entry.get("details_text", "")
                for entry in harness.logs
            )
        )

    def test_ai_consistency_result_uses_request_response_trace(self):
        harness = _AutoWorkflowHarness(_ValidatorDialogStub({}, allow_auto_fix=False), {})

        harness._on_auto_consistency_finished(
            {
                "problems_count": 2,
                "problems_by_confidence": {"high": 1, "medium": 1, "low": 0},
                "fixed_count": 0,
                "fixable_problems_count": 1,
                "auto_fix": True,
                "selected_confidences": ["high"],
                "problem_chapters": ["chapter_01.xhtml"],
                "fixable_problem_chapters": ["chapter_01.xhtml"],
                "fixed_chapters": [],
                "request_response_trace": [
                    {
                        "phase": "analysis",
                        "chapter_names": ["chapter_01.xhtml"],
                        "prompt": "Analyze chapter_01 for consistency issues.",
                        "response": "{\"problems\": [{\"chapter\": \"chapter_01.xhtml\"}]}",
                        "metadata": {"chunk_index": 1, "total_chunks": 1, "mode": "standard"},
                    }
                ],
            }
        )

        self.assertEqual(harness.reset_calls, 1)
        self.assertEqual(harness.ready_calls, 1)
        self.assertTrue(
            any(
                entry["message"].startswith("AI-consistency завершён")
                and "Analyze chapter_01 for consistency issues." in entry.get("details_text", "")
                and "\"problems\"" in entry.get("details_text", "")
                for entry in harness.logs
            )
        )

    def test_run_auto_validator_followup_revalidates_validated_files(self):
        harness = _RunAutoValidatorHarness()
        auto_settings = {"retry_short_enabled": True}
        _AutoValidatorDialogLaunchStub.instances.clear()

        with patch(
            "gemini_translator.ui.dialogs.validation.TranslationValidatorDialog",
            _AutoValidatorDialogLaunchStub,
        ), patch(
            "gemini_translator.ui.dialogs.setup.QtCore.QEventLoop",
            _EventLoopStub,
        ), patch(
            "gemini_translator.ui.dialogs.setup.QtCore.QTimer.singleShot",
            lambda interval_ms, callback: None,
        ):
            harness._run_auto_validator_followup(auto_settings)

        self.assertEqual(len(_AutoValidatorDialogLaunchStub.instances), 1)
        dialog = _AutoValidatorDialogLaunchStub.instances[0]
        self.assertTrue(dialog.hide_called)
        self.assertTrue(dialog.check_show_all.isChecked())
        self.assertTrue(dialog.check_revalidate_ok.isChecked())
        self.assertEqual(dialog.start_analysis_calls, 1)
        self.assertEqual(dialog.start_analysis_targets, [("Text/ch1.xhtml",)])
        self.assertIs(harness._auto_validator_dialog, dialog)
        self.assertEqual(harness.start_btn.enabled_values, [False])
        self.assertEqual(harness.reset_calls, 0)
        self.assertEqual(harness.ready_calls, 0)
        self.assertEqual(harness.finished_calls, [])

    def test_auto_restart_without_active_keys_logs_and_unlocks(self):
        harness = _StartTranslationHarness(has_pending_tasks=True, active_keys=[])

        with patch(
            "gemini_translator.ui.dialogs.setup.QMessageBox.warning",
            side_effect=AssertionError("auto restart should not show a modal warning"),
        ):
            harness._start_translation(is_auto_restart=True)

        self.assertEqual(harness.engine.task_manager.release_calls, 0)
        self.assertEqual(harness.reset_calls, 1)
        self.assertEqual(harness.ready_calls, 1)
        self.assertFalse(harness._auto_followup_running)
        self.assertTrue(
            any("нет активной сессии сервиса/ключей" in entry["message"] for entry in harness.logs)
        )

    def test_auto_restart_without_pending_tasks_logs_and_unlocks(self):
        harness = _StartTranslationHarness(has_pending_tasks=False, active_keys=["stub-key"])

        with patch(
            "gemini_translator.ui.dialogs.setup.QMessageBox.warning",
            side_effect=AssertionError("auto restart should not show a modal warning"),
        ):
            harness._start_translation(is_auto_restart=True)

        self.assertEqual(harness.engine.task_manager.release_calls, 0)
        self.assertEqual(harness.reset_calls, 1)
        self.assertEqual(harness.ready_calls, 1)
        self.assertFalse(harness._auto_followup_running)
        self.assertTrue(
            any("нет задач в очереди" in entry["message"] for entry in harness.logs)
        )


class _AutoLogDispatchHarness:
    _auto_log = InitialSetupDialog._auto_log

    def __init__(self, settings):
        self.auto_translate_widget = _AutoTranslateWidgetStub(settings)
        self.posted_events = []

    def _post_event(self, name, data=None):
        self.posted_events.append((name, data or {}))


class AutoLogDispatchTests(unittest.TestCase):
    def test_auto_log_passes_details_payload(self):
        harness = _AutoLogDispatchHarness({"log_each_step": True})

        harness._auto_log(
            "Проверочный лог",
            details_title="[AUTO] Тест",
            details_text="line-1\nline-2",
            file_path="C:/temp/test.log",
            file_label="open log",
        )

        self.assertEqual(len(harness.posted_events), 1)
        event_name, payload = harness.posted_events[0]
        self.assertEqual(event_name, "log_message")
        self.assertEqual(payload["message"], "[AUTO] Проверочный лог")
        self.assertEqual(payload["details_title"], "[AUTO] Тест")
        self.assertEqual(payload["details_text"], "line-1\nline-2")
        self.assertEqual(payload["file_path"], "C:/temp/test.log")
        self.assertEqual(payload["file_label"], "open log")


class _SignalStub:
    def __init__(self):
        self._callbacks = []

    def connect(self, callback):
        self._callbacks.append(callback)

    def emit(self, *args, **kwargs):
        for callback in list(self._callbacks):
            callback(*args, **kwargs)


class _ButtonStub:
    def __init__(self):
        self.enabled_values = []

    def setEnabled(self, value):
        self.enabled_values.append(bool(value))


class _AutoConsistencyWorkerStub:
    instances = []

    def __init__(
        self,
        settings_manager,
        chapters,
        config,
        active_keys,
        auto_fix,
        mode="standard",
        parent=None,
    ):
        self.settings_manager = settings_manager
        self.chapters = list(chapters)
        self.config = dict(config)
        self.active_keys = list(active_keys)
        self.auto_fix = auto_fix
        self.mode = mode
        self.parent = parent
        self.started = False
        self.finished_with_result = _SignalStub()
        self.failed = _SignalStub()
        self.finished = _SignalStub()
        self.progress_message = _SignalStub()
        self.__class__.instances.append(self)

    def start(self):
        self.started = True


class _AutoConsistencyFollowupHarness:
    _run_auto_consistency_followup = InitialSetupDialog._run_auto_consistency_followup
    _compose_auto_details = InitialSetupDialog._compose_auto_details

    def __init__(self):
        self.project_manager = object()
        self.settings_manager = object()
        self.selected_file = "C:/project/book.epub"
        self.key_management_widget = _KeyManagementWidgetStub()
        self.start_btn = _ButtonStub()
        self.prevent_sleep_checkbox = _CheckStateStub()
        self.logs = []
        self._auto_followup_running = False
        self._auto_consistency_worker = None
        self.reset_calls = 0
        self.ready_calls = 0
        self.finished_results = []
        self.failed_errors = []

    def _get_effective_auto_model_settings(self, auto_settings):
        return {"thinking_level": "low"}

    def _auto_log(self, message, force=False, **kwargs):
        entry = {"message": message, "force": force}
        entry.update(kwargs)
        self.logs.append(entry)

    def _format_auto_chapter_list(self, chapters, limit=10, preserve_order=False):
        return ", ".join(list(chapters)[:limit])

    def _reset_auto_workflow_state(self):
        self.reset_calls += 1

    def check_ready(self):
        self.ready_calls += 1

    def _on_auto_consistency_finished(self, result):
        self.finished_results.append(dict(result))

    def _on_auto_consistency_failed(self, error_text):
        self.failed_errors.append(str(error_text))


class _ConsistencyEngineStub:
    def __init__(self, chapter_problems_map):
        self.error_occurred = _SignalStub()
        self.log_message = _SignalStub()
        self.progress_updated = _SignalStub()
        self.chapter_problems_map = {
            chapter_name: [dict(problem) for problem in problems]
            for chapter_name, problems in chapter_problems_map.items()
        }
        self.all_problems = []
        self.fix_all_calls = []
        self.analyze_calls = []
        self.request_response_trace = [
            {
                "phase": "analysis",
                "chapter_names": sorted(self.chapter_problems_map.keys()),
                "prompt": "stub analysis prompt",
                "response": "stub analysis response",
                "metadata": {"chunk_index": 1, "total_chunks": 1, "mode": "standard"},
            }
        ]

    def analyze_chapters(self, chapters, config, active_keys, mode):
        self.analyze_calls.append(
            {
                "chapters": list(chapters),
                "config": dict(config),
                "active_keys": list(active_keys),
                "mode": mode,
            }
        )
        self.all_problems = [
            dict(problem)
            for problems in self.chapter_problems_map.values()
            for problem in problems
        ]

    def fix_all_chapters(self, chapters, config, active_keys):
        self.fix_all_calls.append(
            {
                "chapters": list(chapters),
                "config": dict(config),
                "active_keys": list(active_keys),
            }
        )
        fixed_files = {}
        for chapter in chapters:
            problems = self.chapter_problems_map.get(chapter["name"], [])
            fixable = filter_consistency_problems_by_confidence(
                problems,
                config.get("consistency_fix_confidences"),
            )
            if fixable:
                fixed_files[chapter["path"]] = chapter["content"] + f"\n<!-- fixed {chapter['name']} -->"
        return fixed_files

    def get_request_response_trace(self):
        return [dict(item) for item in self.request_response_trace]

    def close_session_resources(self):
        return None


class AutoTranslateWidgetConsistencyModeTests(unittest.TestCase):
    def test_ai_consistency_mode_combo_exposes_fast_proofread(self):
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        widget = AutoTranslateWidget(settings_manager=_AutoTranslateSettingsManagerStub())
        try:
            fast_index = widget.ai_consistency_mode_combo.findData(FAST_PROOFREAD_MODE)

            self.assertNotEqual(fast_index, -1)
            widget.ai_consistency_checkbox.setChecked(True)
            widget.ai_consistency_mode_combo.setCurrentIndex(fast_index)
            self.assertEqual(widget.get_settings()["ai_consistency_mode"], FAST_PROOFREAD_MODE)
        finally:
            widget.deleteLater()
            self.assertIsNotNone(app)


class AutoConsistencyFollowupTests(unittest.TestCase):
    def setUp(self):
        _AutoConsistencyWorkerStub.instances.clear()

    def test_run_auto_consistency_followup_passes_selected_levels_to_worker(self):
        harness = _AutoConsistencyFollowupHarness()
        chapters = [{"name": "chapter1.xhtml", "content": "<p>one</p>", "path": "C:/temp/ch1.xhtml"}]
        auto_settings = {
            "ai_consistency_auto_fix": True,
            "ai_consistency_fix_confidences": ["high"],
            "ai_consistency_chunk_size": 5,
            "ai_consistency_mode": "glossary_first",
        }

        with patch(
            "gemini_translator.ui.dialogs.setup.load_project_chapters_for_consistency",
            return_value=chapters,
        ), patch(
            "gemini_translator.ui.dialogs.setup.AutoConsistencyWorker",
            _AutoConsistencyWorkerStub,
        ):
            harness._run_auto_consistency_followup(auto_settings)

        self.assertEqual(len(_AutoConsistencyWorkerStub.instances), 1)
        worker = _AutoConsistencyWorkerStub.instances[0]
        self.assertTrue(worker.started)
        self.assertEqual(worker.config["provider"], "stub-provider")
        self.assertEqual(worker.config["chunk_size"], 5)
        self.assertEqual(worker.config["consistency_mode"], "deep_consistency")
        self.assertEqual(worker.config["consistency_fix_confidences"], ["high"])
        self.assertTrue(worker.auto_fix)
        self.assertEqual(worker.mode, "glossary_first")
        self.assertIs(harness._auto_consistency_worker, worker)
        self.assertTrue(harness._auto_followup_running)
        self.assertEqual(harness.start_btn.enabled_values, [False])
        self.assertTrue(
            any("AI-consistency автофикс по уровням уверенности: high." in entry["message"] for entry in harness.logs)
        )

    def test_run_auto_consistency_followup_passes_fast_proofread_mode(self):
        harness = _AutoConsistencyFollowupHarness()
        chapters = [{"name": "chapter1.xhtml", "content": "<p>one</p>", "path": "C:/temp/ch1.xhtml"}]
        auto_settings = {
            "ai_consistency_auto_fix": False,
            "ai_consistency_chunk_size": 1,
            "ai_consistency_mode": FAST_PROOFREAD_MODE,
        }

        with patch(
            "gemini_translator.ui.dialogs.setup.load_project_chapters_for_consistency",
            return_value=chapters,
        ), patch(
            "gemini_translator.ui.dialogs.setup.AutoConsistencyWorker",
            _AutoConsistencyWorkerStub,
        ):
            harness._run_auto_consistency_followup(auto_settings)

        worker = _AutoConsistencyWorkerStub.instances[0]
        self.assertEqual(worker.config["consistency_mode"], FAST_PROOFREAD_MODE)
        self.assertEqual(worker.mode, FAST_PROOFREAD_MODE)

    def test_run_auto_consistency_followup_passes_original_flag_to_loader(self):
        harness = _AutoConsistencyFollowupHarness()
        chapters = [
            {
                "name": "chapter1.xhtml",
                "content": "<p>one</p>",
                "source_content": "<p>一</p>",
                "path": "C:/temp/ch1.xhtml",
            }
        ]
        auto_settings = {
            "ai_consistency_auto_fix": False,
            "ai_consistency_use_original": True,
            "ai_consistency_original_chapter_limit": 2,
            "ai_consistency_chunk_size": 1,
        }

        with patch(
            "gemini_translator.ui.dialogs.setup.load_project_chapters_for_consistency",
            return_value=chapters,
        ) as loader, patch(
            "gemini_translator.ui.dialogs.setup.AutoConsistencyWorker",
            _AutoConsistencyWorkerStub,
        ):
            harness._run_auto_consistency_followup(auto_settings)

        loader.assert_called_once_with(
            harness.project_manager,
            original_epub_path=harness.selected_file,
            include_original=True,
        )
        worker = _AutoConsistencyWorkerStub.instances[0]
        self.assertTrue(worker.config["consistency_include_original"])
        self.assertEqual(worker.config["consistency_original_chapter_limit"], 2)
        self.assertTrue(
            any("будет сверять перевод с оригиналом EPUB" in entry["message"] for entry in harness.logs)
        )

    def test_run_auto_consistency_followup_migrates_legacy_source_context_setting(self):
        harness = _AutoConsistencyFollowupHarness()
        chapters = [
            {
                "name": "chapter1.xhtml",
                "content": "<p>one</p>",
                "source_content": "<p>source</p>",
                "path": "C:/temp/ch1.xhtml",
            }
        ]
        auto_settings = {
            "ai_consistency_auto_fix": False,
            "source_context_enabled": True,
            "source_context_chapters": 4,
        }

        with patch(
            "gemini_translator.ui.dialogs.setup.load_project_chapters_for_consistency",
            return_value=chapters,
        ) as loader, patch(
            "gemini_translator.ui.dialogs.setup.AutoConsistencyWorker",
            _AutoConsistencyWorkerStub,
        ):
            harness._run_auto_consistency_followup(auto_settings)

        loader.assert_called_once_with(
            harness.project_manager,
            original_epub_path=harness.selected_file,
            include_original=True,
        )
        worker = _AutoConsistencyWorkerStub.instances[0]
        self.assertTrue(worker.config["consistency_include_original"])
        self.assertEqual(worker.config["consistency_original_chapter_limit"], 4)

    def test_run_auto_consistency_followup_keeps_empty_level_selection(self):
        harness = _AutoConsistencyFollowupHarness()
        chapters = [{"name": "chapter1.xhtml", "content": "<p>one</p>", "path": "C:/temp/ch1.xhtml"}]
        auto_settings = {
            "ai_consistency_auto_fix": True,
            "ai_consistency_fix_confidences": [],
            "ai_consistency_chunk_size": 2,
        }

        with patch(
            "gemini_translator.ui.dialogs.setup.load_project_chapters_for_consistency",
            return_value=chapters,
        ), patch(
            "gemini_translator.ui.dialogs.setup.AutoConsistencyWorker",
            _AutoConsistencyWorkerStub,
        ):
            harness._run_auto_consistency_followup(auto_settings)

        self.assertEqual(len(_AutoConsistencyWorkerStub.instances), 1)
        worker = _AutoConsistencyWorkerStub.instances[0]
        self.assertEqual(worker.config["consistency_fix_confidences"], [])
        self.assertTrue(
            any("AI-consistency автофикс по уровням уверенности: ничего не исправлять." in entry["message"] for entry in harness.logs)
        )


class AutoConsistencyWorkerTests(unittest.TestCase):
    def test_worker_applies_only_selected_confidence_levels(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            chapter1_path = os.path.join(temp_dir, "chapter1.xhtml")
            chapter2_path = os.path.join(temp_dir, "chapter2.xhtml")
            with open(chapter1_path, "w", encoding="utf-8") as handle:
                handle.write("<p>chapter one</p>")
            with open(chapter2_path, "w", encoding="utf-8") as handle:
                handle.write("<p>chapter two</p>")

            chapters = [
                {"name": "chapter1.xhtml", "content": "<p>chapter one</p>", "path": chapter1_path},
                {"name": "chapter2.xhtml", "content": "<p>chapter two</p>", "path": chapter2_path},
            ]
            engine_stub = _ConsistencyEngineStub(
                {
                    "chapter1.xhtml": [{"id": "p1", "confidence": "high"}],
                    "chapter2.xhtml": [{"id": "p2", "confidence": "low"}],
                }
            )
            results = []
            failures = []

            with patch(
                "gemini_translator.ui.dialogs.auto_workflow.ConsistencyEngine",
                lambda settings_manager: engine_stub,
            ):
                worker = AutoConsistencyWorker(
                    settings_manager=object(),
                    chapters=chapters,
                    config={"consistency_fix_confidences": ["high"]},
                    active_keys=["stub-key"],
                    auto_fix=True,
                )
                worker.finished_with_result.connect(results.append)
                worker.failed.connect(failures.append)
                worker.run()

            self.assertEqual(failures, [])
            self.assertEqual(len(engine_stub.fix_all_calls), 1)
            self.assertEqual(len(results), 1)
            result = results[0]
            self.assertEqual(result["problems_count"], 2)
            self.assertEqual(result["selected_confidences"], ["high"])
            self.assertEqual(result["fixable_problems_count"], 1)
            self.assertEqual(result["problem_chapters"], ["chapter1.xhtml", "chapter2.xhtml"])
            self.assertEqual(result["fixable_problem_chapters"], ["chapter1.xhtml"])
            self.assertEqual(result["fixed_count"], 1)
            self.assertEqual(result["fixed_chapters"], ["chapter1.xhtml"])
            self.assertEqual(result["request_response_trace"][0]["prompt"], "stub analysis prompt")
            with open(chapter1_path, "r", encoding="utf-8") as handle:
                self.assertIn("fixed chapter1.xhtml", handle.read())
            with open(chapter2_path, "r", encoding="utf-8") as handle:
                self.assertNotIn("fixed chapter2.xhtml", handle.read())

    def test_worker_skips_auto_fix_when_no_levels_selected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            chapter_path = os.path.join(temp_dir, "chapter.xhtml")
            original_content = "<p>chapter</p>"
            with open(chapter_path, "w", encoding="utf-8") as handle:
                handle.write(original_content)

            chapters = [{"name": "chapter.xhtml", "content": original_content, "path": chapter_path}]
            engine_stub = _ConsistencyEngineStub(
                {
                    "chapter.xhtml": [{"id": "p1", "confidence": "high"}],
                }
            )
            results = []
            failures = []

            with patch(
                "gemini_translator.ui.dialogs.auto_workflow.ConsistencyEngine",
                lambda settings_manager: engine_stub,
            ):
                worker = AutoConsistencyWorker(
                    settings_manager=object(),
                    chapters=chapters,
                    config={"consistency_fix_confidences": []},
                    active_keys=["stub-key"],
                    auto_fix=True,
                )
                worker.finished_with_result.connect(results.append)
                worker.failed.connect(failures.append)
                worker.run()

            self.assertEqual(failures, [])
            self.assertEqual(len(engine_stub.fix_all_calls), 0)
            self.assertEqual(len(results), 1)
            result = results[0]
            self.assertEqual(result["selected_confidences"], [])
            self.assertEqual(result["fixable_problems_count"], 0)
            self.assertEqual(result["fixable_problem_chapters"], [])
            self.assertEqual(result["fixed_count"], 0)
            self.assertEqual(result["request_response_trace"][0]["response"], "stub analysis response")
            with open(chapter_path, "r", encoding="utf-8") as handle:
                self.assertEqual(handle.read(), original_content)


if __name__ == "__main__":
    unittest.main()
