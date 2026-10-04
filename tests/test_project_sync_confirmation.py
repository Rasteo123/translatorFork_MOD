import json
import threading
import zipfile

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QMessageBox, QWidget

from gemini_translator.ui.dialogs import epub
from gemini_translator.ui.wait_dialogs import show_when_slow
from gemini_translator.utils.project_manager import TranslationProjectManager
from gemini_translator.utils.project_migrator import QuestionHandler, SyncThread


def test_sync_releases_question_handler_after_finishing(qtbot):
    class EmptyMigrator:
        def ensure_project_is_modern_and_synced(self, communicator):
            return True, "Done"

    widget = QWidget()
    qtbot.addWidget(widget)
    thread = SyncThread(EmptyMigrator(), parent_widget=widget)
    thread.start()
    assert thread.wait(2000)
    qtbot.waitUntil(lambda: not widget.findChildren(QuestionHandler), timeout=300)


def test_immediate_answers_are_not_lost_or_reused(qapp):
    """An answer delivered before wait() must still release the worker."""
    answers = iter([True, False])
    received = []

    class AskingMigrator:
        def ensure_project_is_modern_and_synced(self, communicator):
            received.append(communicator.ask_cleanup(["missing"]))
            received.append(communicator.ask_add_untracked(["new"]))
            return True, "Done"

    thread = SyncThread(AskingMigrator())
    # Deliver a real response synchronously at the request boundary: this is
    # the ordering that used to lose wakeAll() before condition.wait().
    thread._ask_question_in_ui_thread.disconnect()
    thread._ask_question_in_ui_thread.connect(
        lambda *args: thread._set_user_response(next(answers)),
        Qt.ConnectionType.DirectConnection,
    )
    thread.start()
    try:
        assert thread.wait(500), "The worker lost an answer delivered before wait()"
        assert received == [True, False]
    finally:
        for _ in range(4):
            thread._set_user_response(False)
            if thread.wait(100):
                break
        assert not thread.isRunning()


def test_wait_resumes_between_questions_and_stays_closed_after_completion(qtbot, monkeypatch):
    analysis_gate = threading.Event()
    received = []

    class TwoQuestionMigrator:
        def ensure_project_is_modern_and_synced(self, communicator):
            received.append(communicator.ask_cleanup(["missing"]))
            assert analysis_gate.wait(3)
            received.append(communicator.ask_cleanup_ghosts(["ghost"]))
            return True, "Done"

    widget = QWidget()
    qtbot.addWidget(widget)
    widget.show()
    monkeypatch.setattr(epub, "ProjectMigrator", lambda *args: TwoQuestionMigrator())
    monkeypatch.setattr(epub, "show_when_slow", lambda dialog: show_when_slow(dialog, 30))
    completed = []

    def finished(ready, message):
        widget.wait_dialog.accept()
        completed.append(ready)

    epub.run_project_migrator_sync(widget, None, "", "", "Sync", "Wait", finished)

    def active_question():
        return next((
            box for box in widget.findChildren(QMessageBox)
            if box.isVisible() and box is not widget.wait_dialog and box.buttons()
        ), None)

    try:
        assert widget.sync_thread._question_handler.thread() is QApplication.instance().thread()
        qtbot.waitUntil(lambda: active_question() is not None)
        first = active_question()
        qtbot.wait(100)
        assert QApplication.activeModalWidget() is first
        next(button for button in first.buttons() if button.text() == "Да").click()
        qtbot.waitUntil(widget.wait_dialog.isVisible)
        analysis_gate.set()
        qtbot.waitUntil(lambda: active_question() is not None)
        second = active_question()
        qtbot.wait(100)
        assert not widget.wait_dialog.isVisible()
        assert QApplication.activeModalWidget() is second
        next(button for button in second.buttons() if button.text() == "Нет").click()
        qtbot.waitUntil(lambda: bool(completed))
        assert completed == [True]
        assert received == [True, False]
        qtbot.wait(100)
        assert not widget.wait_dialog.isVisible()
    finally:
        analysis_gate.set()
        for box in widget.findChildren(QMessageBox):
            box.reject()
        for _ in range(4):
            widget.sync_thread._set_user_response(False)
            if widget.sync_thread.wait(100):
                break
        assert not widget.sync_thread.isRunning()


@pytest.mark.parametrize("answer", ["Да", "Нет", "close"])
@pytest.mark.parametrize("wait_already_visible", [False, True])
def test_deleted_chapter_confirmation_remains_accessible(
    qtbot, tmp_path, monkeypatch, answer, wait_already_visible
):
    """The wait timer must not block confirmation, before or after it fires."""
    original = "OEBPS/Text/chapter1.xhtml"
    missing_translation = "OEBPS/Text/chapter1_translated.html"
    initial_map = {original: {"_translated.html": missing_translation}}
    map_path = tmp_path / "translation_map.json"
    map_path.write_text(json.dumps(initial_map), encoding="utf-8")
    book = tmp_path / "book.epub"
    with zipfile.ZipFile(book, "w") as archive:
        archive.writestr(original, "<html><body>Chapter</body></html>")

    analysis_gate = threading.Event()
    if not wait_already_visible:
        analysis_gate.set()

    class ControlledManager(TranslationProjectManager):
        def validate_map_with_filesystem(self):
            assert analysis_gate.wait(3)
            return super().validate_map_with_filesystem()

    manager = ControlledManager(str(tmp_path))
    widget = QWidget()
    qtbot.addWidget(widget)
    widget.show()
    monkeypatch.setattr(epub, "show_when_slow", lambda dialog: show_when_slow(dialog, 30))
    results = []

    def finished(ready, message):
        widget.wait_dialog.accept()
        results.append((ready, message))

    epub.run_project_migrator_sync(
        widget, manager, str(tmp_path), str(book), "Синхронизация", "Анализ", finished
    )
    try:
        if wait_already_visible:
            qtbot.waitUntil(widget.wait_dialog.isVisible, timeout=2000)
            analysis_gate.set()
        qtbot.waitUntil(
            lambda: any(
                box.isVisible() and box is not widget.wait_dialog and box.buttons()
                for box in widget.findChildren(QMessageBox)
            ), timeout=2000,
        )
        question = next(
            box for box in widget.findChildren(QMessageBox)
            if box.isVisible() and box is not widget.wait_dialog and box.buttons()
        )
        # Let the delayed wait timer expire while the user reads the question.
        qtbot.wait(100)
        assert not widget.wait_dialog.isVisible()
        assert QApplication.activeModalWidget() is question
        if answer == "close":
            question.close()
        else:
            next(button for button in question.buttons() if button.text() == answer).click()
        qtbot.waitUntil(lambda: bool(results), timeout=2000)
        assert results[0][0] is True
        expected_map = {} if answer == "Да" else initial_map
        assert json.loads(map_path.read_text(encoding="utf-8")) == expected_map
        qtbot.wait(100)
        assert not widget.wait_dialog.isVisible()
    finally:
        analysis_gate.set()
        for box in widget.findChildren(QMessageBox):
            box.reject()
        # Also release the worker on failure, so a regression cannot hang pytest.
        widget.sync_thread._set_user_response(False)
        assert widget.sync_thread.wait(3000)
        manager.flush()
