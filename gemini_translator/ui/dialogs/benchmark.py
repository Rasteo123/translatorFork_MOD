from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import traceback
from typing import Any

from PyQt6 import QtCore, QtGui, QtWidgets

from ...api import config as api_config
from ...benchmark.runner import BenchmarkRunner
from ...utils.document_importer import DOCUMENT_INPUT_FILTER, extract_document_chapters


class BenchmarkRunWorker(QtCore.QThread):
    progress_changed = QtCore.pyqtSignal(dict)
    finished_ok = QtCore.pyqtSignal(dict)
    failed = QtCore.pyqtSignal(str)

    def __init__(
        self,
        *,
        config_path: str,
        output_dir: str | None,
        prompt_only: bool,
        save_prompts: bool,
        filters: dict[str, set[str]],
        limit: int | None,
        api_keys_by_provider: dict[str, list[str]],
        parent=None,
    ):
        super().__init__(parent)
        self.config_path = config_path
        self.output_dir = output_dir
        self.prompt_only = prompt_only
        self.save_prompts = save_prompts
        self.filters = filters
        self.limit = limit
        self.api_keys_by_provider = api_keys_by_provider

    def run(self):
        try:
            runner = BenchmarkRunner(
                self.config_path,
                output_dir=self.output_dir or None,
                prompt_only=self.prompt_only,
                save_prompts=self.save_prompts,
                filters=self.filters,
                limit=self.limit,
                api_keys_by_provider=self.api_keys_by_provider,
                progress_callback=self.progress_changed.emit,
            )
            report = runner.run()
            self.finished_ok.emit(report)
        except Exception:
            self.failed.emit(traceback.format_exc())


class PromptBenchmarkDialog(QtWidgets.QDialog):
    """PyQt interface for editing and running prompt/model benchmark configs."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Бенчмарк промптов и моделей")
        self.setMinimumSize(1180, 780)

        from gemini_translator.ui.pages.benchmark_page import PromptBenchmarkPage

        self.page = PromptBenchmarkPage(self)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.page)
