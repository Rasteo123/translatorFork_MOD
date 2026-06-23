"""Regression for the 2026-06-23 SIGABRT crash.

CorrectionSessionPage starts a ``GlossaryFrequencyWorker`` (a QThread) parented
to the page. The navigation shell tears pages down via
``NavigationController.pop()`` -> ``page.on_leave()`` -> ``page.deleteLater()``.

The page used to stop the worker only in ``reject()`` / the legacy dialog
``closeEvent`` -- neither of which the shell calls. So navigating Back while the
worker ran destroyed a still-running QThread, and Qt aborted the process with
"QThread: Destroyed while thread is still running".

The fix: ``CorrectionSessionPage`` overrides ``on_leave()`` to stop the worker.
"""
import importlib
import os
import sys
import types
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _import_ai_correction():
    """Import ai_correction without tripping the widgets/glossary circular import."""
    module_name = "gemini_translator.ui.widgets.glossary_widget"
    previous_module = sys.modules.get(module_name)
    fake_module = types.ModuleType(module_name)
    fake_module.GlossaryWidget = type("_FakeGlossaryWidget", (), {})
    sys.modules[module_name] = fake_module
    try:
        return importlib.import_module(
            "gemini_translator.ui.dialogs.glossary_dialogs.ai_correction"
        )
    finally:
        if previous_module is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous_module


CorrectionSessionPage = _import_ai_correction().CorrectionSessionPage


class _FakeWorker:
    """Stand-in for GlossaryFrequencyWorker (cooperative stop + wait)."""

    def __init__(self):
        self._running = True
        self.stopped = False
        self.waited = False

    def isRunning(self):
        return self._running

    def stop(self):
        self.stopped = True
        self._running = False

    def wait(self, ms=0):
        self.waited = True
        return True


def test_correction_page_overrides_on_leave():
    from gemini_translator.ui.shell import ShellPage

    # The bug was that the page inherited the base no-op on_leave, so the shell
    # never stopped the worker before deleteLater().
    assert CorrectionSessionPage.on_leave is not ShellPage.on_leave


def test_on_leave_stops_running_frequency_worker():
    worker = _FakeWorker()
    # Bypass the heavy QWidget __init__: on_leave only touches Python attrs.
    page = SimpleNamespace(_frequency_worker=worker)
    page._stop_frequency_worker = lambda: CorrectionSessionPage._stop_frequency_worker(page)

    CorrectionSessionPage.on_leave(page)

    assert worker.stopped, "on_leave must stop the frequency worker"
    assert worker.waited, "on_leave must wait for the worker to finish"
    assert page._frequency_worker is None
