"""The quality section must drive the QA runtime and report honestly."""

from __future__ import annotations

import asyncio
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6 import QtWidgets

from gemini_translator.core.chapter_qa_coordinator import (
    BookQaResult,
    TranslationReadyEvent,
)
from gemini_translator.qa.journal import QaJournal
from gemini_translator.qa.models import ChapterMetrics, RiskLevel
from gemini_translator.qa.repair_store import UndoResult
from gemini_translator.qa.service import ChapterQaResult
from gemini_translator.ui.dialogs.validation_dialogs.translation_quality_controller import (
    TranslationQualityController,
)


@pytest.fixture(scope="module")
def qt_app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class _Coordinator:
    """Runs the same coroutines the real one does, but on this thread."""

    def __init__(self, *, book_result=None, undo_result=None, error=None) -> None:
        self.book_result = book_result
        self.undo_result = undo_result or UndoResult("restored", ("chapter-1",))
        self.error = error
        self.cancelled = False
        self.reset_calls = 0
        self.checked: list[str] = []

    def run_background(self, factory, on_done=None):
        try:
            result = asyncio.run(factory())
            error = None
        except Exception as failure:  # noqa: BLE001 - mirrors the real callback
            result, error = None, failure
        if on_done is not None:
            on_done(result, error)

    def reset_cancellation(self):
        self.reset_calls += 1

    def cancel(self):
        self.cancelled = True

    async def check_chapter_now(self, event, options=None):
        if self.error is not None:
            raise self.error
        self.checked.append(event.chapter_id)
        return ChapterQaResult(
            chapter_id=event.chapter_id,
            risk_level=RiskLevel.LOW,
            may_continue_translation=True,
            coverage_mode="semantic_alignment",
        )

    async def check_all_now(self, events, options=None):
        if self.error is not None:
            raise self.error
        self.checked.extend(event.chapter_id for event in events)
        return self.book_result or BookQaResult(
            results=tuple(
                ChapterQaResult(
                    chapter_id=event.chapter_id,
                    risk_level=RiskLevel.LOW,
                    may_continue_translation=True,
                    coverage_mode="semantic_alignment",
                )
                for event in events
            )
        )

    async def undo_chapter(self, chapter_id):
        return self.undo_result

    async def undo_all(self):
        return self.undo_result


def _journal() -> QaJournal:
    journal = QaJournal.empty(book_id="book-1")
    journal.upsert_metrics(
        ChapterMetrics(
            chapter_id="chapter-1",
            source_language="zh",
            target_language="ru",
            source_chars=100,
            translated_chars=290,
        )
    )
    return journal


def _event(chapter_id: str) -> TranslationReadyEvent:
    return TranslationReadyEvent(
        task_id="manual",
        chapter_id=chapter_id,
        source_path=chapter_id,
        translated_path=f"/tmp/{chapter_id}.html",
        source_language="auto",
        target_language="ru",
    )


def _controller(coordinator, *, events=("chapter-1",), journal_loader=None):
    return TranslationQualityController(
        coordinator_provider=lambda: coordinator,
        journal_loader=journal_loader or _journal,
        gates_provider=lambda: (),
        event_builder=lambda chapter_ids: tuple(
            _event(chapter_id)
            for chapter_id in (chapter_ids or events)
            if chapter_id in events
        ),
    )


def test_report_is_rebuilt_from_the_journal(qt_app):
    """The report must come from the durable record, not from memory."""
    controller = _controller(_Coordinator())
    snapshots = []
    controller.report_ready.connect(snapshots.append)

    controller.refresh_report()

    assert snapshots
    assert [row.chapter_id for row in snapshots[-1].rows] == ["chapter-1"]


def test_a_broken_journal_is_reported_not_swallowed(qt_app):
    """A damaged decision history must be visible, not an empty report."""

    def broken():
        raise RuntimeError("journal is corrupted")

    controller = _controller(_Coordinator(), journal_loader=broken)
    statuses = []
    controller.status_changed.connect(statuses.append)

    controller.refresh_report()

    assert statuses and "corrupted" in statuses[-1]


def test_checking_one_chapter_runs_and_refreshes(qt_app):
    """One manual check must use the same cascade and update the report."""
    coordinator = _Coordinator()
    controller = _controller(coordinator)
    statuses, busy = [], []
    controller.status_changed.connect(statuses.append)
    controller.busy_changed.connect(busy.append)

    controller.check_chapter("chapter-1")

    assert coordinator.checked == ["chapter-1"]
    assert busy == [True, False]
    assert "chapter-1" in statuses[-1]


def test_checking_a_chapter_without_a_translation_costs_nothing(qt_app):
    """A chapter with no saved translation must not start a pass."""
    coordinator = _Coordinator()
    controller = _controller(coordinator)
    statuses = []
    controller.status_changed.connect(statuses.append)

    controller.check_chapter("chapter-404")

    assert coordinator.checked == []
    assert "нет сохранённого перевода" in statuses[-1]


def test_book_pass_reports_counts_and_blocked_chapters(qt_app):
    """A whole-book pass must say what it did and what still needs a decision."""
    coordinator = _Coordinator(
        book_result=BookQaResult(
            results=(
                ChapterQaResult(
                    chapter_id="chapter-1",
                    risk_level=RiskLevel.HIGH,
                    may_continue_translation=False,
                    coverage_mode="semantic_alignment",
                ),
            ),
            skipped=("chapter-2",),
        )
    )
    controller = _controller(coordinator, events=("chapter-1", "chapter-2"))
    statuses = []
    controller.status_changed.connect(statuses.append)

    controller.check_all()

    assert coordinator.reset_calls == 1
    assert "Проверено глав: 1 из 2" in statuses[-1]
    assert "Пропущено: 1" in statuses[-1]
    assert "chapter-1" in statuses[-1]


def test_a_failing_pass_is_reported_and_clears_busy(qt_app):
    """A crash inside QA must release the interface and say what happened."""
    controller = _controller(_Coordinator(error=RuntimeError("boom")))
    statuses, busy = [], []
    controller.status_changed.connect(statuses.append)
    controller.busy_changed.connect(busy.append)

    controller.check_chapter("chapter-1")

    assert busy == [True, False]
    assert "boom" in statuses[-1]


def test_undo_reports_restored_and_conflicting_chapters(qt_app):
    """Undo must distinguish what it restored from what a human had edited."""
    coordinator = _Coordinator(
        undo_result=UndoResult("manual_edit_conflict", ("chapter-1",))
    )
    controller = _controller(coordinator)
    statuses = []
    controller.status_changed.connect(statuses.append)

    controller.undo_chapter("chapter-1")

    assert "вручную" in statuses[-1]
    assert "chapter-1" in statuses[-1]


def test_cancel_stops_the_runtime_and_the_busy_state(qt_app):
    """Cancelling must reach the runtime, not only the interface."""
    coordinator = _Coordinator()
    controller = _controller(coordinator)
    controller.check_all()

    controller.cancel()

    assert coordinator.cancelled is True


def test_missing_setup_is_explained_instead_of_failing_silently(qt_app):
    """Without a configured QA runtime the user must learn what to set up."""
    controller = TranslationQualityController(
        coordinator_provider=lambda: None,
        journal_loader=_journal,
        event_builder=lambda chapter_ids: (_event("chapter-1"),),
    )
    statuses = []
    controller.status_changed.connect(statuses.append)

    controller.check_all()

    assert "не настроена" in statuses[-1]


def test_attaching_a_dialog_connects_both_directions(qt_app):
    """The dialog's actions and the controller's report must be wired once."""
    from gemini_translator.ui.dialogs.validation_dialogs import TranslationQualityDialog

    coordinator = _Coordinator()
    controller = _controller(coordinator)
    dialog = TranslationQualityDialog()

    controller.attach(dialog)

    assert dialog.table_model.rowCount() == 1
    dialog.select_chapter("chapter-1")
    dialog.check_chapter_requested.emit("chapter-1")
    assert coordinator.checked == ["chapter-1"]


def test_embedding_probe_refuses_an_incomplete_setup(qt_app):
    """Testing a connection that cannot be built must explain, not throw."""
    from gemini_translator.qa.settings import QaSettings

    controller = _controller(_Coordinator())
    statuses = []
    controller.status_changed.connect(statuses.append)

    controller.test_embedding(QaSettings(embedding_provider="openai_compatible"))

    assert statuses and "ключ" in statuses[-1].lower()


def test_embedding_probe_reports_a_provider_that_cannot_be_built(qt_app):
    """A provider with nothing configured must be named as a setup problem."""
    from gemini_translator.qa.settings import QaSettings
    from gemini_translator.ui.dialogs.validation_dialogs.translation_quality_controller import (
        _probe_embedding,
    )

    message = _probe_embedding(QaSettings(embedding_provider="gemini"))

    assert "не настроен" in message


def test_embedding_probe_reports_a_missing_local_model(qt_app):
    """Choosing the local model without installing it must say exactly that."""
    from gemini_translator.qa.settings import QaSettings
    from gemini_translator.ui.dialogs.validation_dialogs.translation_quality_controller import (
        _probe_embedding,
    )

    message = _probe_embedding(QaSettings(embedding_provider="local_onnx"))

    assert "не удалось" in message.lower() or "не настроен" in message.lower()


def test_export_writes_the_bundle_and_reports_where(qt_app, tmp_path):
    """An export the user cannot find is not an export."""
    controller = _controller(_Coordinator())
    statuses = []
    controller.status_changed.connect(statuses.append)

    controller.export_report(str(tmp_path))

    assert (tmp_path / "chapters.csv").is_file()
    assert (tmp_path / "glossary.csv").is_file()
    assert str(tmp_path) in statuses[-1]


def test_export_reports_a_broken_journal_instead_of_writing_nothing(qt_app, tmp_path):
    """A silent no-op would look exactly like a successful export."""

    def broken():
        raise RuntimeError("journal is corrupted")

    controller = _controller(_Coordinator(), journal_loader=broken)
    statuses = []
    controller.status_changed.connect(statuses.append)

    controller.export_report(str(tmp_path))

    assert "corrupted" in statuses[-1]
    assert not list(tmp_path.glob("*.csv"))
