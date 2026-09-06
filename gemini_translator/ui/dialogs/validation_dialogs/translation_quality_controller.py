# -*- coding: utf-8 -*-
"""Drive the quality dialog from the QA runtime without blocking the interface."""

from __future__ import annotations

import asyncio
import threading
import time

from PyQt6.QtCore import QObject, pyqtSignal

from ....utils.text import format_duration
from .translation_quality_models import BookQaReportSnapshot


# How often a running pass may reread the journal to refresh the table.  Often
# enough that the report grows while the user watches, rarely enough that a
# six-hundred-chapter book does not spend its time reading its own journal.
REPORT_REFRESH_SECONDS = 4.0


# Why a chapter is being checked again, in the words of the person reading it.
RECHECK_REASONS = {
    "never_checked": "ещё не проверялась",
    "deferred": "проверка не завершилась",
    "unresolved_risk": "остался неустранённый риск",
    "analysis_version_changed": "правила проверки изменились",
    "baseline_now_available": "появилась норма книги",
}


def _escape(value: str) -> str:
    from html import escape

    return escape(str(value or ""))


class TranslationQualityController(QObject):
    """Connect one dialog to one QA coordinator, in both directions.

    Every long operation runs on the QA runtime; results come back as Qt signals
    so the dialog only ever touches immutable snapshots on the UI thread.
    """

    report_ready = pyqtSignal(object)
    status_changed = pyqtSignal(str)
    busy_changed = pyqtSignal(bool)
    progress_changed = pyqtSignal(int, int, str)
    # One finished chapter, already rendered: what was found, what was changed,
    # and what was only suggested.  A pass over a book runs for hours, and the
    # report used to appear only when it ended.
    chapter_logged = pyqtSignal(str)

    def __init__(
        self,
        *,
        coordinator_provider,
        journal_loader,
        gates_provider=None,
        event_builder=None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        if not callable(coordinator_provider):
            raise TypeError("coordinator_provider must be callable")
        if not callable(journal_loader):
            raise TypeError("journal_loader must be callable")
        self._coordinator_provider = coordinator_provider
        self._journal_loader = journal_loader
        self._gates_provider = gates_provider
        self._event_builder = event_builder
        self._busy = False
        self._last_report_refresh = 0.0

    # -- wiring ------------------------------------------------------------

    def attach(self, dialog) -> None:
        """Bind one dialog's four actions and keep its report up to date."""
        dialog.check_chapter_requested.connect(self.check_chapter)
        dialog.check_all_requested.connect(self.check_all)
        if hasattr(dialog, "resume_requested"):
            dialog.resume_requested.connect(self.resume)
        dialog.undo_chapter_requested.connect(self.undo_chapter)
        dialog.undo_all_requested.connect(self.undo_all)
        dialog.cancel_requested.connect(self.cancel)
        dialog.embedding_test_requested.connect(self.test_embedding)
        dialog.export_requested.connect(self.export_report)
        self.report_ready.connect(dialog.set_report)
        self.status_changed.connect(dialog.set_status)
        self.busy_changed.connect(dialog.set_busy)
        self.progress_changed.connect(dialog.set_progress)
        if hasattr(dialog, "append_log"):
            self.chapter_logged.connect(dialog.append_log)
        self.refresh_report()

    # -- actions -----------------------------------------------------------

    def refresh_report(self) -> None:
        """Rebuild the report from the durable journal and the open gates."""
        try:
            journal = self._journal_loader()
        except Exception as error:  # noqa: BLE001 - a broken journal is reportable
            self.status_changed.emit(f"Журнал проверок недоступен: {error}")
            return
        gates = ()
        if callable(self._gates_provider):
            try:
                gates = self._gates_provider() or ()
            except Exception:  # noqa: BLE001 - gates are advisory for the report
                gates = ()
        self.report_ready.emit(BookQaReportSnapshot.from_journal(journal, gates))

    def check_chapter(self, chapter_id: str) -> None:
        """Run the full cascade for one chapter, exactly as the session does."""
        events = self._events_for([chapter_id])
        if not events:
            self.status_changed.emit(
                f"Для главы «{chapter_id}» нет сохранённого перевода."
            )
            return
        coordinator = self._coordinator()
        if coordinator is None:
            return
        self._set_busy(True)
        self.progress_changed.emit(0, 1, chapter_id)
        coordinator.run_background(
            lambda: coordinator.check_chapter_now(events[0]),
            lambda result, error: self._finish_check(result, error, 1, 1, chapter_id),
        )

    def check_all(self) -> None:
        """Run the cascade over every chapter that has a saved translation."""
        events = self._events_for(None)
        if not events:
            self.status_changed.emit("Нет сохранённых переводов для проверки.")
            return
        coordinator = self._coordinator()
        if coordinator is None:
            return
        coordinator.reset_cancellation()
        self._set_busy(True)
        self.progress_changed.emit(0, len(events), "")
        self._pass_started = time.perf_counter()
        self._last_report_refresh = 0.0
        self.chapter_logged.emit(
            f"<p><b>Проверка книги: {len(events)} глав(ы).</b></p>"
        )
        coordinator.run_background(
            lambda: coordinator.check_all_now(
                events,
                on_progress=self._on_chapter_done,
                on_chapter=self._log_chapter,
            ),
            lambda result, error: self._finish_book_pass(result, error, len(events)),
        )

    def resume(self) -> None:
        """Check only the chapters whose last check no longer answers for them.

        A pass over a book runs for hours, and closing the window in the middle
        of one loses nothing: every finished chapter is already in the journal.
        Starting over would pay for those chapters a second time, so this asks
        the journal what is actually left.
        """
        events = self._events_for(None)
        if not events:
            self.status_changed.emit("Нет сохранённых переводов для проверки.")
            return
        coordinator = self._coordinator()
        if coordinator is None:
            return
        try:
            selected = coordinator.select_unsettled_chapters(events)
        except Exception as error:  # noqa: BLE001 - a broken journal is reportable
            self.status_changed.emit(f"Не удалось прочитать журнал проверок: {error}")
            return
        if not selected:
            self.status_changed.emit(
                f"Перепроверять нечего: все {len(events)} глав(ы) уже улажены."
            )
            return
        pending = tuple(item.event for item in selected)
        coordinator.reset_cancellation()
        self._set_busy(True)
        self.progress_changed.emit(0, len(pending), "")
        self._pass_started = time.perf_counter()
        self._last_report_refresh = 0.0
        self.chapter_logged.emit(self._resume_header(selected, len(events)))
        coordinator.run_background(
            lambda: coordinator.check_all_now(
                pending,
                on_progress=self._on_chapter_done,
                on_chapter=self._log_chapter,
            ),
            lambda result, error: self._finish_book_pass(result, error, len(pending)),
        )

    @staticmethod
    def _resume_header(selected, total: int) -> str:
        """Say how much of the book is being re-checked, and why."""
        counts: dict[str, int] = {}
        for item in selected:
            reason = str(getattr(item, "reason", "") or "")
            counts[reason] = counts.get(reason, 0) + 1
        parts = ", ".join(
            f"{RECHECK_REASONS.get(reason, reason)} — {count}"
            for reason, count in sorted(counts.items(), key=lambda pair: -pair[1])
        )
        settled = max(0, total - len(selected))
        tail = f" Пропущено как улаженные: {settled}." if settled else ""
        return (
            f"<p><b>Продолжаем проверку: {len(selected)} глав(ы) из {total}.</b>"
            f"{_escape(tail)}<br>{_escape(parts)}</p>"
        )

    def undo_chapter(self, chapter_id: str) -> None:
        """Restore one chapter to the translation the model produced."""
        coordinator = self._coordinator()
        if coordinator is None:
            return
        self._set_busy(True)
        coordinator.run_background(
            lambda: coordinator.undo_chapter(chapter_id),
            lambda result, error: self._finish_undo(result, error),
        )

    def undo_all(self) -> None:
        """Restore every chapter this project repaired automatically."""
        coordinator = self._coordinator()
        if coordinator is None:
            return
        self._set_busy(True)
        coordinator.run_background(
            lambda: coordinator.undo_all(),
            lambda result, error: self._finish_undo(result, error),
        )

    def export_report(self, directory: str) -> None:
        """Write the book's report next to wherever the user asked for it."""
        from ....qa.reporting import QaReportBuilder

        try:
            journal = self._journal_loader()
        except Exception as error:  # noqa: BLE001 - a broken journal is reportable
            self.status_changed.emit(f"Журнал проверок недоступен: {error}")
            return
        try:
            written = QaReportBuilder().export_csv_bundle(directory, journal)
        except OSError as error:
            self.status_changed.emit(f"Не удалось сохранить отчёт: {error}")
            return
        self.status_changed.emit(
            f"Отчёт сохранён: {len(written)} файла(ов) в {directory}"
        )

    def test_embedding(self, qa_settings) -> None:
        """Send one tiny embedding request so a wrong key is found here, not later."""
        problem = qa_settings.embedding_setup_problem()
        if problem:
            self.status_changed.emit(problem)
            return
        self.status_changed.emit("Проверяем подключение…")

        def run() -> None:
            message = _probe_embedding(qa_settings)
            self.status_changed.emit(message)

        threading.Thread(target=run, name="qa-embedding-probe", daemon=True).start()

    def cancel(self) -> None:
        """Ask the running pass to stop at its next safe point."""
        coordinator = self._coordinator(quiet=True)
        if coordinator is not None:
            coordinator.cancel()
        self.status_changed.emit("Проверка остановлена.")
        self._set_busy(False)

    # -- internals ---------------------------------------------------------

    def _coordinator(self, quiet: bool = False):
        try:
            coordinator = self._coordinator_provider()
        except Exception as error:  # noqa: BLE001 - setup failures are reportable
            coordinator = None
            if not quiet:
                self.status_changed.emit(f"Проверка недоступна: {error}")
            return None
        if coordinator is None and not quiet:
            # Not necessarily the embeddings: outside a translation session
            # there is no runtime at all until one is built for the project.
            self.status_changed.emit(
                "Проверка недоступна: не настроена модель проверки или ключ к ней."
            )
        return coordinator

    def _events_for(self, chapter_ids):
        if not callable(self._event_builder):
            return ()
        try:
            return tuple(self._event_builder(chapter_ids) or ())
        except Exception as error:  # noqa: BLE001 - a bad project map is reportable
            self.status_changed.emit(f"Не удалось собрать список глав: {error}")
            return ()

    def _log_chapter(self, result) -> None:
        """Render one finished chapter into the running log.

        A chapter that changed nothing still gets a line: over six hundred
        chapters, silence is indistinguishable from a check that died.
        """
        if result is None:
            return
        chapter_id = str(getattr(result, "chapter_id", "") or "")
        try:
            interesting = bool(getattr(result, "changed_anything", False))
            language = getattr(result, "language", None)
            unchecked = int(getattr(language, "unchecked_blocks", 0) or 0)
            suggestions = len(getattr(language, "suggestions", ()) or ())
            blocked = not getattr(result, "may_continue_translation", True)
            if interesting or unchecked or blocked or suggestions:
                self.chapter_logged.emit(result.change_details_html())
            else:
                self.chapter_logged.emit(
                    f"<p><b>{_escape(chapter_id)}</b> — без изменений.</p>"
                )
        except Exception:  # noqa: BLE001 - the log never fails a check
            self.chapter_logged.emit(f"<p><b>{_escape(chapter_id)}</b></p>")

    def _refresh_report_throttled(self) -> None:
        """Fill the table while the pass runs, without rereading the journal per chapter."""
        now = time.perf_counter()
        if now - self._last_report_refresh < REPORT_REFRESH_SECONDS:
            return
        self._last_report_refresh = now
        self.refresh_report()

    def _on_chapter_done(self, done: int, total: int, chapter_id: str) -> None:
        """Show which chapter just finished and what the rest is likely to cost.

        The estimate is the pass's own pace so far, not a guess about the book:
        one chapter's duration says little, so nothing is promised until two
        have finished.
        """
        label = str(chapter_id or "")
        elapsed = time.perf_counter() - getattr(self, "_pass_started", time.perf_counter())
        remaining = total - done
        if done >= 2 and remaining > 0 and elapsed > 0:
            seconds = int(remaining * elapsed / done)
            label = f"{label} · осталось ~{_humanize_seconds(seconds)}" if label else (
                f"осталось ~{_humanize_seconds(seconds)}"
            )
        self.progress_changed.emit(done, total, label)
        self._refresh_report_throttled()

    def _finish_check(
        self, result, error, checked: int, total: int, chapter_id: str
    ) -> None:
        if error is not None:
            self.status_changed.emit(f"Проверка не удалась: {error}")
        else:
            self._log_chapter(result)
            self.progress_changed.emit(checked, total, chapter_id)
            self.status_changed.emit(f"Глава «{chapter_id}» проверена.")
        self._set_busy(False)
        self.refresh_report()

    def _finish_book_pass(self, result, error, total: int) -> None:
        if error is not None:
            self.status_changed.emit(f"Проверка книги не удалась: {error}")
        else:
            checked = len(getattr(result, "results", ()) or ())
            skipped = getattr(result, "skipped", ()) or ()
            blocking = getattr(result, "blocking_chapters", ()) or ()
            self.progress_changed.emit(checked, total, "")
            message = f"Проверено глав: {checked} из {total}."
            if skipped:
                message += f" Пропущено: {len(skipped)}."
            if blocking:
                message += " Требуют решения: " + ", ".join(blocking[:5])
            self.status_changed.emit(message)
        self._set_busy(False)
        self.refresh_report()

    def _finish_undo(self, result, error) -> None:
        if error is not None:
            self.status_changed.emit(f"Откат не удался: {error}")
        else:
            status = getattr(result, "status", "")
            chapters = getattr(result, "chapters", ()) or ()
            if status == "restored":
                self.status_changed.emit(
                    "Восстановлены исходные версии: " + ", ".join(chapters)
                )
            elif status == "manual_edit_conflict":
                self.status_changed.emit(
                    "Главы изменены вручную после исправления и оставлены без "
                    "изменений: " + ", ".join(chapters)
                )
            else:
                self.status_changed.emit("Отменять нечего.")
        self._set_busy(False)
        self.refresh_report()

    def _set_busy(self, busy: bool) -> None:
        if self._busy == bool(busy):
            return
        self._busy = bool(busy)
        self.busy_changed.emit(self._busy)


def _probe_embedding(qa_settings) -> str:
    """Return a human-readable verdict about the configured embedding provider."""
    from ....qa.assembly import aiohttp_session_factory, build_embedding_provider
    from ....qa.embeddings.base import EmbeddingRequest

    try:
        provider = build_embedding_provider(
            qa_settings, aiohttp_session_factory(), {}
        )
    except Exception as error:  # noqa: BLE001 - the user needs the reason, not a trace
        return f"Провайдер не настроен: {error}"
    request = EmbeddingRequest(
        texts=("Проверка подключения.",),
        language="ru",
        model=qa_settings.embedding_model or "gemini-embedding-001",
        task_type="semantic-similarity",
    )
    try:
        batch = asyncio.run(provider.embed(request))
    except Exception as error:  # noqa: BLE001 - any failure is a plain answer here
        return f"Подключение не удалось: {type(error).__name__}"
    return (
        f"Подключение работает: {batch.provider}, модель {batch.model}, "
        f"{batch.dimensions} измерений."
    )


def _humanize_seconds(seconds: int) -> str:
    """Say a duration the way a person waiting for it would.

    Thin alias kept because tests import this name directly; the actual
    formatting lives in the shared ``format_duration`` helper
    (gemini_translator/utils/text.py), which every other duration display
    in the project also goes through.
    """
    return format_duration(seconds, round_minutes=True, seconds_unit="с")
