"""A pass over a book must be readable while it runs, not only when it ends.

Measured against the real window: 634 chapters, «Проверено 8 из 634» on the
progress bar and «Глав в отчёте: 0» above an empty table, because the report was
rebuilt from the journal only in the finish handler.  The edits themselves were
never in this window at all — only in the application log behind it.
"""

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
from gemini_translator.qa.language_validation import LanguageQaResult
from gemini_translator.qa.llm.schemas import LanguageIssue
from gemini_translator.qa.models import RiskLevel
from gemini_translator.qa.service import ChapterQaResult
from gemini_translator.ui.dialogs.validation_dialogs.translation_quality_controller import (
    TranslationQualityController,
)


@pytest.fixture(scope="module")
def qt_app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _replacement(issue_id: str, before: str, after: str):
    from gemini_translator.qa.language_validation import LanguageReplacement

    return LanguageReplacement(
        issue_id=issue_id, block_id="b-1", original_text=before, replacement_text=after
    )


def _issue(issue_id: str, before: str, after: str) -> LanguageIssue:
    return LanguageIssue(
        issue_id=issue_id,
        category="typo",
        block_id="b-1",
        original_text=before,
        replacement_text=after,
        objective=True,
        confidence=1.0,
        explanation="Опечатка.",
    )


def _repaired(chapter_id: str) -> ChapterQaResult:
    return ChapterQaResult(
        chapter_id=chapter_id,
        risk_level=RiskLevel.LOW,
        may_continue_translation=True,
        coverage_mode="semantic_alignment",
        language=LanguageQaResult(
            chapter_id=chapter_id,
            issues=(_issue("i-1", "скзал", "сказал"),),
            applied=(_replacement("i-1", "скзал", "сказал"),),
            blocks_total=10,
        ),
    )


def _clean(chapter_id: str) -> ChapterQaResult:
    return ChapterQaResult(
        chapter_id=chapter_id,
        risk_level=RiskLevel.LOW,
        may_continue_translation=True,
        coverage_mode="semantic_alignment",
        language=LanguageQaResult(chapter_id=chapter_id, blocks_total=10),
    )


class _Coordinator:
    """Runs the same coroutines the real one does, on this thread."""

    def __init__(self, results) -> None:
        self.results = list(results)

    def run_background(self, factory, on_done=None):
        try:
            result, error = asyncio.run(factory()), None
        except Exception as failure:  # noqa: BLE001 - mirrors the real callback
            result, error = None, failure
        if on_done is not None:
            on_done(result, error)

    def reset_cancellation(self):
        return None

    def cancel(self):
        return None

    async def check_chapter_now(self, event, options=None):
        return self.results[0]

    async def check_all_now(self, events, options=None, on_progress=None, on_chapter=None):
        for index, (event, result) in enumerate(zip(events, self.results), start=1):
            if callable(on_chapter):
                on_chapter(result)
            if callable(on_progress):
                on_progress(index, len(events), event.chapter_id)
        return BookQaResult(tuple(self.results), ())


def _controller(results, *, journal_loads=None) -> TranslationQualityController:
    coordinator = _Coordinator(results)

    def load_journal():
        if journal_loads is not None:
            journal_loads.append(True)
        return QaJournal(book_id="book", updated_at="2026-08-31T00:00:00Z")

    return TranslationQualityController(
        coordinator_provider=lambda: coordinator,
        journal_loader=load_journal,
        event_builder=lambda chapter_ids: tuple(
            TranslationReadyEvent(
                task_id="t",
                chapter_id=chapter_id,
                source_path=chapter_id,
                translated_path=chapter_id,
                source_language="zh",
                target_language="ru",
                epub_path="book.epub",
            )
            for chapter_id in (
                chapter_ids
                if chapter_ids is not None
                else [result.chapter_id for result in results]
            )
        ),
    )


def _collect(controller) -> list[str]:
    entries: list[str] = []
    controller.chapter_logged.connect(entries.append)
    return entries


def test_every_finished_chapter_says_what_it_changed(qt_app):
    """Ради этого всё и делалось: видно, в какой главе что на что исправилось."""
    controller = _controller([_repaired("chapter-1"), _repaired("chapter-2")])
    entries = _collect(controller)

    controller.check_all()

    joined = "\n".join(entries)
    assert "chapter-1" in joined and "chapter-2" in joined
    assert "скзал" in joined and "сказал" in joined
    assert "Языковые исправления" in joined


def test_a_chapter_with_nothing_to_fix_still_reports_itself(qt_app):
    """На шестистах главах молчание неотличимо от умершей проверки."""
    controller = _controller([_clean("chapter-1")])
    entries = _collect(controller)

    controller.check_all()

    assert any("chapter-1" in entry and "без изменений" in entry for entry in entries)


def test_the_pass_announces_how_much_work_it_took_on(qt_app):
    """Первая строка журнала — сколько глав проверяется."""
    controller = _controller([_clean("chapter-1")])
    entries = _collect(controller)

    controller.check_all()

    assert entries and "Проверка книги" in entries[0]


def test_one_chapter_checked_by_hand_lands_in_the_same_log(qt_app):
    """Кнопка «проверить главу» — тот же журнал, а не отдельная судьба."""
    controller = _controller([_repaired("chapter-1")])
    entries = _collect(controller)

    controller.check_chapter("chapter-1")

    assert any("сказал" in entry for entry in entries)


def test_the_report_fills_while_the_pass_runs(qt_app):
    """Таблица не должна оставаться пустой три часа подряд."""
    loads: list[bool] = []
    controller = _controller([_clean(f"chapter-{n}") for n in range(1, 4)], journal_loads=loads)
    snapshots: list[object] = []
    controller.report_ready.connect(snapshots.append)

    controller.check_all()

    # One refresh during the pass and one when it finishes: the table exists
    # long before the last chapter.
    assert len(snapshots) >= 2
    assert loads


def test_the_report_is_not_rebuilt_once_per_chapter(qt_app):
    """Перечитывать журнал на каждой главе книги — это и есть та тормозящая проверка."""
    loads: list[bool] = []
    controller = _controller(
        [_clean(f"chapter-{n}") for n in range(1, 41)], journal_loads=loads
    )

    controller.check_all()

    # Forty chapters in well under the refresh interval: one throttled refresh
    # plus the final one.
    assert len(loads) <= 3


def test_the_dialog_shows_the_log_and_keeps_the_newest_in_view(qt_app):
    """Журнал живёт в самом окне проверки, а не в логе приложения за ним."""
    from gemini_translator.ui.dialogs.validation_dialogs import TranslationQualityDialog

    dialog = TranslationQualityDialog()
    try:
        assert "Журнал правок" in [
            dialog.tabs.tabText(index) for index in range(dialog.tabs.count())
        ]

        dialog.append_log("<p><b>chapter-1</b><br>было: скзал<br>стало: сказал</p>")
        dialog.append_log("<p><b>chapter-2</b> — без изменений.</p>")

        text = dialog.log_view.toPlainText()
        assert "скзал" in text and "chapter-2" in text
        assert dialog.log_view.document().maximumBlockCount() > 0
    finally:
        dialog.deleteLater()


def test_an_empty_entry_never_pushes_a_blank_line_into_the_log(qt_app):
    from gemini_translator.ui.dialogs.validation_dialogs import TranslationQualityDialog

    dialog = TranslationQualityDialog()
    try:
        dialog.append_log("")
        dialog.append_log("   ")

        assert dialog.log_view.toPlainText().strip() == ""
    finally:
        dialog.deleteLater()
