# -*- coding: utf-8 -*-

import os

from PyQt6 import QtCore

from ...core.consistency_engine import (
    CONSISTENCY_CONFIDENCE_LEVELS,
    ConsistencyEngine,
    filter_consistency_problems_by_confidence,
    normalize_consistency_confidence,
    normalize_consistency_confidences,
)


def choose_preferred_translation_rel_path(versions: dict) -> str | None:
    if not isinstance(versions, dict) or not versions:
        return None

    if versions.get(""):
        return versions.get("")
    if versions.get("_validated.html"):
        return versions.get("_validated.html")

    for suffix, rel_path in versions.items():
        if suffix != "filtered" and rel_path:
            return rel_path

    return next(iter(versions.values()), None)


def load_project_chapters_for_consistency(project_manager) -> list[dict]:
    if not project_manager:
        return []

    chapters_to_analyze = []
    project_folder = project_manager.project_folder

    for internal_path in project_manager.get_all_originals():
        versions = project_manager.get_versions_for_original(internal_path)
        rel_path = choose_preferred_translation_rel_path(versions)
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

        chapters_to_analyze.append({
            "name": os.path.basename(internal_path),
            "content": content,
            "path": full_path,
        })

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
        try:
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
            if engine is not None:
                engine.close_session_resources()
