"""Снимок очереди queue_snapshot.db в папке книги.

Раньше снимок писался так: старый файл удалялся, затем backup() копировал базу
из памяти страница в страницу. backup переносит и страницы, освободившиеся
после удаления задач, поэтому файл разрастался до пикового размера очереди
(у реальных книг 54 МБ файла при 0,7 МБ данных) и целиком перезаписывался
на каждом автосохранении. А пока шло копирование, на диске не было ни старого
снимка, ни нового.
"""

import os
import sqlite3
import uuid
from contextlib import closing

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets  # noqa: E402

from gemini_translator.core.task_manager import ChapterQueueManager  # noqa: E402

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class _Bus:
    def subscribe(self, *_args, **_kwargs):
        pass

    def emit_event(self, *_args, **_kwargs):
        pass


def _fail_replace(*_args):
    raise OSError("disk full")


@pytest.fixture
def queue(tmp_path):
    uri = f"file:queue_snapshot_{uuid.uuid4().hex}?mode=memory&cache=shared"
    anchor = sqlite3.connect(uri, uri=True, check_same_thread=False)
    anchor.row_factory = sqlite3.Row
    manager = ChapterQueueManager(event_bus=_Bus(), db_uri=uri, main_connection=anchor)
    # Папки книг почти всегда названы кириллицей, а путь снимка уходит в SQL
    # (VACUUM INTO) — пусть тесты идут по такому же пути.
    book_folder = tmp_path / "Книга"
    book_folder.mkdir()
    epub = book_folder / "book.epub"
    epub.write_bytes(b"PK\x03\x04" + b"chapter text" * 100)
    yield manager, str(epub), book_folder
    manager.shutdown()
    anchor.close()


def _chapters(epub, names):
    return [("epub", epub, name) for name in names]


def test_snapshot_holds_no_pages_freed_by_removed_tasks(queue):
    manager, epub, book_folder = queue
    manager.add_pending_tasks(
        _chapters(epub, [f"Text/old{index:04d}.xhtml" for index in range(2000)])
    )
    manager.clear_all_queues()
    manager.add_pending_tasks(
        _chapters(epub, ["Text/kept1.xhtml", "Text/kept2.xhtml", "Text/kept3.xhtml"])
    )
    snapshot = book_folder / "queue_snapshot.db"

    assert manager.save_queue_snapshot(str(snapshot), epub) is True

    with closing(sqlite3.connect(snapshot)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 3
        assert conn.execute("PRAGMA freelist_count").fetchone()[0] == 0


def test_failed_snapshot_save_keeps_previous_snapshot(queue, monkeypatch):
    manager, epub, book_folder = queue
    manager.add_pending_tasks(_chapters(epub, ["Text/ch1.xhtml", "Text/ch2.xhtml"]))
    snapshot = book_folder / "queue_snapshot.db"
    assert manager.save_queue_snapshot(str(snapshot), epub) is True
    previous = snapshot.read_bytes()

    manager.add_pending_tasks(_chapters(epub, ["Text/ch3.xhtml"]))
    monkeypatch.setattr(os, "replace", _fail_replace)

    assert manager.save_queue_snapshot(str(snapshot), epub) is False
    assert snapshot.read_bytes() == previous
    assert sorted(path.name for path in book_folder.iterdir()) == [
        "book.epub",
        "queue_snapshot.db",
    ]


def test_saved_snapshot_restores_queue_for_the_same_book(queue):
    manager, epub, book_folder = queue
    manager.add_pending_tasks(
        _chapters(epub, ["Text/ch1.xhtml", "Text/ch2.xhtml", "Text/ch3.xhtml"])
    )
    snapshot = book_folder / "queue_snapshot.db"
    assert manager.save_queue_snapshot(str(snapshot), epub) is True
    manager.clear_all_queues()

    restored = manager.load_queue_snapshot(str(snapshot), epub)

    assert restored == ["Text/ch1.xhtml", "Text/ch2.xhtml", "Text/ch3.xhtml"]
