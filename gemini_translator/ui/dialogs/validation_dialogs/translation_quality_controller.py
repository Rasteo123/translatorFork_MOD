# -*- coding: utf-8 -*-
"""Drive the quality dialog from the QA runtime without blocking the interface."""

from __future__ import annotations

import asyncio
import threading

from PyQt6.QtCore import QObject, pyqtSignal

from .translation_quality_models import BookQaReportSnapshot


class TranslationQualityController(QObject):
    """Connect one dialog to one QA coordinator, in both directions.

    Every long operation runs on the QA runtime; results come back as Qt signals
    so the dialog only ever touches immutable snapshots on the UI thread.
    """

    report_ready = pyqtSignal(object)
    status_changed = pyqtSignal(str)
    busy_changed = pyqtSignal(bool)
    progress_changed = pyqtSignal(int, int, str)

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

    # -- wiring ------------------------------------------------------------

    def attach(self, dialog) -> None:
        """Bind one dialog's four actions and keep its report up to date."""
        dialog.check_chapter_requested.connect(self.check_chapter)
        dialog.check_all_requested.connect(self.check_all)
        dialog.undo_chapter_requested.connect(self.undo_chapter)
        dialog.undo_all_requested.connect(self.undo_all)
        dialog.cancel_requested.connect(self.cancel)
        dialog.embedding_test_requested.connect(self.test_embedding)
        dialog.export_requested.connect(self.export_report)
        self.report_ready.connect(dialog.set_report)
        self.status_changed.connect(dialog.set_status)
        self.busy_changed.connect(dialog.set_busy)
        self.progress_changed.connect(dialog.set_progress)
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
            lambda result, error: self._finish_check(error, 1, 1, chapter_id),
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
        coordinator.run_background(
            lambda: coordinator.check_all_now(events),
            lambda result, error: self._finish_book_pass(result, error, len(events)),
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
            self.status_changed.emit(
                "Проверка качества не настроена: укажите ключ и модель для эмбеддингов."
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

    def _finish_check(self, error, checked: int, total: int, chapter_id: str) -> None:
        if error is not None:
            self.status_changed.emit(f"Проверка не удалась: {error}")
        else:
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
