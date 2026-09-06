# -*- coding: utf-8 -*-

import os
import zipfile

from PyQt6 import QtCore

from ...core.consistency_engine import (
    CONSISTENCY_CONFIDENCE_LEVELS,
    ConsistencyEngine,
    filter_consistency_problems_by_confidence,
    normalize_consistency_confidence,
    normalize_consistency_confidences,
)
from ...utils.power_inhibitor import PREVENT_SLEEP_SETTING_KEY, PowerInhibitor
from ...utils.translation_versions import select_target_translation_version


def choose_preferred_translation_rel_path(versions: dict) -> str | None:
    """Совместимость с ui/dialogs/setup.py, который импортирует это имя, но
    нигде его не вызывает (мёртвый импорт вне зоны ответственности этой
    правки). Реальный выбор версии перевода главы теперь идёт через
    канонический select_target_translation_version (см.
    load_project_chapters_for_consistency ниже); эта обёртка оставлена
    только ради обратной совместимости импорта и не используется в этом
    модуле."""
    if not isinstance(versions, dict) or not versions:
        return None
    rel_path, _is_validated = select_target_translation_version(versions, "")
    return rel_path


def load_project_chapters_for_consistency(
    project_manager,
    *,
    original_epub_path: str | None = None,
    include_original: bool = False,
) -> list[dict]:
    if not project_manager:
        return []

    chapters_to_analyze = []
    project_folder = project_manager.project_folder
    original_by_path = {}
    all_originals = list(project_manager.get_all_originals())

    if include_original and original_epub_path and os.path.exists(original_epub_path):
        original_paths = [
            str(path or "").replace("\\", "/")
            for path in all_originals
            if str(path or "").strip()
        ]
        try:
            with open(original_epub_path, "rb") as epub_file, zipfile.ZipFile(epub_file, "r") as epub_zip:
                available_names = {name.replace("\\", "/"): name for name in epub_zip.namelist()}
                for internal_path in original_paths:
                    zip_name = available_names.get(internal_path)
                    if not zip_name:
                        continue
                    try:
                        original_by_path[internal_path] = epub_zip.read(zip_name).decode("utf-8", "ignore")
                    except (KeyError, OSError, UnicodeError):
                        continue
        except (OSError, zipfile.BadZipFile):
            original_by_path = {}

    for internal_path in all_originals:
        normalized_internal_path = str(internal_path or "").replace("\\", "/")
        versions = project_manager.get_versions_for_original(internal_path)
        rel_path, _is_validated = select_target_translation_version(versions, project_folder)
        if not rel_path:
            continue

        full_path = os.path.join(project_folder, rel_path)
        if not os.path.exists(full_path):
            continue

        try:
            with open(full_path, "r", encoding="utf-8") as f:
                content = f.read()
        except OSError:
            continue

        chapter = {
            "name": os.path.basename(normalized_internal_path),
            "content": content,
            "path": full_path,
        }
        original_content = original_by_path.get(normalized_internal_path)
        if original_content:
            chapter["source_content"] = original_content
            chapter["source_path"] = normalized_internal_path

        chapters_to_analyze.append(chapter)

    return chapters_to_analyze


class AutoConsistencyWorker(QtCore.QThread):
    finished_with_result = QtCore.pyqtSignal(dict)
    failed = QtCore.pyqtSignal(str)
    progress_message = QtCore.pyqtSignal(str)

    def __init__(
        self,
        settings_manager,
        chapters: list[dict],
        config: dict,
        active_keys: list[str],
        auto_fix: bool,
        mode: str = "standard",
        parent=None,
    ):
        super().__init__(parent)
        self.settings_manager = settings_manager
        self.chapters = chapters or []
        self.config = config or {}
        self.active_keys = active_keys or []
        self.auto_fix = auto_fix
        self.mode = mode or "standard"

    def run(self):
        engine = None
        power_inhibitor = PowerInhibitor()
        try:
            if self.config.get(PREVENT_SLEEP_SETTING_KEY):
                if power_inhibitor.prevent_sleep():
                    self.progress_message.emit("[POWER] Сон системы заблокирован на время AI-проверки.")
                elif power_inhibitor.last_error:
                    self.progress_message.emit(
                        f"[POWER-WARN] Не удалось включить защиту от сна: {power_inhibitor.last_error}"
                    )
            engine = ConsistencyEngine(self.settings_manager)
            engine_errors: list[str] = []
            engine.error_occurred.connect(lambda message: engine_errors.append(str(message)))
            engine.log_message.connect(self.progress_message.emit)
            engine.progress_updated.connect(
                lambda current, total: self.progress_message.emit(
                    f"AI-consistency прогресс: {current}/{total}."
                )
            )
            engine.analyze_chapters(self.chapters, self.config, self.active_keys, self.mode)
            if engine_errors:
                unique_errors = []
                for message in engine_errors:
                    if message and message not in unique_errors:
                        unique_errors.append(message)
                raise RuntimeError("\n".join(unique_errors[:3]))
            problems_count = len(engine.all_problems)
            fixed_files = {}
            selected_confidences = normalize_consistency_confidences(
                self.config.get("consistency_fix_confidences"),
                default=CONSISTENCY_CONFIDENCE_LEVELS,
                allow_empty=True,
            )
            problems_by_confidence = {level: 0 for level in CONSISTENCY_CONFIDENCE_LEVELS}
            for problem in engine.all_problems:
                level = normalize_consistency_confidence(problem.get("confidence"))
                problems_by_confidence[level] = problems_by_confidence.get(level, 0) + 1
            problem_chapters = sorted([
                chapter_name
                for chapter_name, problems in engine.chapter_problems_map.items()
                if chapter_name and problems
            ])
            fixable_problem_chapters = sorted([
                chapter_name
                for chapter_name, problems in engine.chapter_problems_map.items()
                if chapter_name and filter_consistency_problems_by_confidence(problems, selected_confidences)
            ])
            fixable_problems_count = sum(
                len(filter_consistency_problems_by_confidence(problems, selected_confidences))
                for problems in engine.chapter_problems_map.values()
            )
            chapter_name_by_path = {
                chapter.get("path"): chapter.get("name") or os.path.basename(chapter.get("path", ""))
                for chapter in self.chapters
                if isinstance(chapter, dict) and chapter.get("path")
            }

            if fixable_problems_count and self.auto_fix:
                fixed_files = engine.fix_all_chapters(self.chapters, self.config, self.active_keys)
                for path, content in fixed_files.items():
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(content)

            fixed_chapters = [
                chapter_name_by_path.get(path, os.path.basename(path))
                for path in fixed_files.keys()
            ]

            self.finished_with_result.emit({
                "problems_count": problems_count,
                "problems_by_confidence": problems_by_confidence,
                "fixed_count": len(fixed_files),
                "auto_fix": self.auto_fix,
                "mode": self.mode,
                "analyzed_count": len(self.chapters),
                "selected_confidences": list(selected_confidences),
                "fixable_problems_count": fixable_problems_count,
                "problem_chapters": problem_chapters,
                "fixable_problem_chapters": fixable_problem_chapters,
                "fixed_chapters": fixed_chapters,
                "request_response_trace": engine.get_request_response_trace(),
            })
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            power_inhibitor.allow_sleep()
            if engine is not None:
                engine.close_session_resources()
