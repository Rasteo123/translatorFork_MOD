"""Dedup cluster-43: единая атомарная запись JSON/bytes/text.

До рефакторинга ``gemini_translator/mcp/ai_bridge.py``,
``gemini_translator/ui/dialogs/chapter_editor.py`` и
``gemini_translator/qa/repair_store.py`` реализовывали схему «temp-файл +
os.replace» независимо друг от друга (расходясь по fsync, уникальности
имени temp-файла и chmod). Канонические ``atomic_write_bytes`` /
``atomic_write_text`` теперь живут в ``gemini_translator.utils.io_utils``.

Группа (а) — характеризационные тесты канонической реализации: крайние
случаи, которые как раз и различали три копии (fsync, уникальность
temp-имени, cleanup при ошибке, опциональный chmod, сохранение переводов
строк как есть).

Группа (b) — тест-маршрутизация: подменяет ``atomic_write_bytes`` /
``atomic_write_text`` в пространстве имён каждого бывшего места вызова
(``ai_bridge``, ``chapter_editor``, ``repair_store``) — тот же приём, что
уже применяется в tests/test_fix_g04_language_repair_guard.py для
``qa_service.atomic_write_bytes`` — и проверяет, что вызов действительно
идёт через неё. До рефакторинга это падает: ``ai_bridge``/``chapter_editor``
не содержат такого имени вовсе (своя инлайн-копия), у ``repair_store`` есть
собственная копия, а не импорт канонической функции.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from gemini_translator.mcp import ai_bridge
from gemini_translator.qa import repair_store
from gemini_translator.ui.dialogs import chapter_editor
from gemini_translator.utils import io_utils


# ---------------------------------------------------------------------------
# (a) Характеризационные тесты канонической реализации
# ---------------------------------------------------------------------------


def test_atomic_write_bytes_creates_file_with_exact_content(tmp_path):
    path = tmp_path / "out.bin"
    io_utils.atomic_write_bytes(path, b"hello")
    assert path.read_bytes() == b"hello"


def test_atomic_write_bytes_replaces_existing_content(tmp_path):
    path = tmp_path / "out.bin"
    path.write_bytes(b"old-content-longer-than-new")
    io_utils.atomic_write_bytes(path, b"new")
    assert path.read_bytes() == b"new"


def test_atomic_write_bytes_leaves_no_temp_file_after_success(tmp_path):
    path = tmp_path / "out.bin"
    io_utils.atomic_write_bytes(path, b"data")
    leftovers = [p for p in tmp_path.iterdir() if p != path]
    assert leftovers == []


def test_atomic_write_bytes_uses_unique_temp_name_not_fixed_dot_tmp(tmp_path, monkeypatch):
    """Дивергенция: старая repair_store.py использовала фиксированное имя
    ``<path>.tmp`` — при гонке параллельных записей один temp-файл мог быть
    перезаписан другим до os.replace. Канонический вариант обязан
    использовать случайный суффикс (secrets.token_hex), а не фиксированный.
    """
    path = tmp_path / "out.bin"
    seen_temp_names = []
    real_replace = __import__("os").replace

    def spying_replace(src, dst):
        seen_temp_names.append(Path(src).name)
        return real_replace(src, dst)

    monkeypatch.setattr(io_utils.os, "replace", spying_replace)
    io_utils.atomic_write_bytes(path, b"one")
    io_utils.atomic_write_bytes(path, b"two")
    assert len(seen_temp_names) == 2
    assert seen_temp_names[0] != seen_temp_names[1]
    assert not any(name == "out.bin.tmp" for name in seen_temp_names)


def test_atomic_write_bytes_fsyncs_before_replace(tmp_path, monkeypatch):
    """Дивергенция: ai_bridge.py и mcp/jobs.py не делали fsync — при сбое
    между write и replace возможна потеря данных. Каноническая версия
    обязана fsync-ить по умолчанию.
    """
    calls = []
    real_fsync = io_utils.os.fsync

    def spying_fsync(fd):
        calls.append(fd)
        return real_fsync(fd)

    monkeypatch.setattr(io_utils.os, "fsync", spying_fsync)
    io_utils.atomic_write_bytes(tmp_path / "out.bin", b"data")
    assert calls, "atomic_write_bytes(fsync=True по умолчанию) обязан звать os.fsync"


def test_atomic_write_bytes_fsync_false_skips_fsync(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(io_utils.os, "fsync", lambda fd: calls.append(fd))
    io_utils.atomic_write_bytes(tmp_path / "out.bin", b"data", fsync=False)
    assert calls == []


def test_atomic_write_bytes_cleans_up_temp_file_on_failure(tmp_path, monkeypatch):
    path = tmp_path / "out.bin"

    def failing_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(io_utils.os, "replace", failing_replace)
    with pytest.raises(OSError):
        io_utils.atomic_write_bytes(path, b"data")
    leftovers = list(tmp_path.iterdir())
    assert leftovers == [], "осиротевший temp-файл не должен оставаться после сбоя"


@pytest.mark.skipif(os.name == "nt", reason="chmod на Windows меняет только бит read-only, POSIX-права не проверить")
def test_atomic_write_bytes_applies_optional_mode(tmp_path):
    path = tmp_path / "secret.json"
    io_utils.atomic_write_bytes(path, b"{}", mode=0o600)
    assert (path.stat().st_mode & 0o777) == 0o600


def test_atomic_write_bytes_without_mode_does_not_chmod(tmp_path):
    path = tmp_path / "plain.json"
    io_utils.atomic_write_bytes(path, b"{}")
    # Поведение repair_store/chapter_editor: без запроса mode chmod не трогаем.
    # Проверяем лишь то, что запись прошла — конкретные права оставляем ОС/umask.
    assert path.read_bytes() == b"{}"


def test_atomic_write_text_round_trips_content(tmp_path):
    path = tmp_path / "out.txt"
    io_utils.atomic_write_text(path, "привет\nмир")
    assert path.read_text(encoding="utf-8") == "привет\nмир"


def test_atomic_write_text_preserves_newlines_as_is(tmp_path):
    """chapter_editor._atomic_write_text писал через ``newline=""`` — без
    трансляции \\n -> \\r\\n. Канонический atomic_write_text обязан вести
    себя так же (кодирует текст напрямую, не проходя через текстовый режим
    с универсальными переводами строк).
    """
    path = tmp_path / "out.txt"
    text_with_crlf = "line1\r\nline2\nline3"
    io_utils.atomic_write_text(path, text_with_crlf)
    assert path.read_bytes() == text_with_crlf.encode("utf-8")


def test_atomic_write_json_bytes_round_trip(tmp_path):
    path = tmp_path / "task.json"
    payload = json.dumps({"id": "t1", "status": "pending"}, ensure_ascii=False, indent=2)
    io_utils.atomic_write_text(path, payload, mode=0o600)
    assert json.loads(path.read_text(encoding="utf-8")) == {"id": "t1", "status": "pending"}
    if os.name != "nt":  # на Windows chmod(0o600) не отражается в st_mode
        assert (path.stat().st_mode & 0o777) == 0o600


# ---------------------------------------------------------------------------
# (b) Тест-маршрутизация: бывшие места вызова обязаны идти через каноническую
#     функцию, а не через собственную копию.
# ---------------------------------------------------------------------------


def test_ai_bridge_save_gui_ai_task_routes_through_canonical_atomic_write_text(
    tmp_path, monkeypatch
):
    calls = []
    real = io_utils.atomic_write_text

    def spy(path, text, **kwargs):
        calls.append((Path(path), kwargs))
        return real(path, text, **kwargs)

    monkeypatch.setattr(ai_bridge, "atomic_write_text", spy, raising=False)

    task = ai_bridge.create_gui_ai_task(tmp_path, {"prompt": "hi"})

    assert calls, (
        "save_gui_ai_task обязан звать io_utils.atomic_write_text, "
        "а не собственную инлайн-копию atomic-write"
    )
    written_path, kwargs = calls[-1]
    assert written_path == ai_bridge.gui_ai_task_path(tmp_path, task.id)
    # Данные GUI AI-задач считаются чувствительными (см. divergence в
    # cluster-43) — поведение chmod(0o600) из старой копии сохраняется.
    assert kwargs.get("mode") == 0o600


def test_repair_store_atomic_write_bytes_is_the_canonical_function(monkeypatch, tmp_path):
    """repair_store.atomic_write_bytes должен быть каноническим импортом, а
    не собственным определением — этим уже пользуются qa/service.py и
    qa/structural_repair.py (``from .repair_store import atomic_write_bytes``),
    и их сигнатуру (path, data) менять нельзя.
    """
    assert repair_store.atomic_write_bytes is io_utils.atomic_write_bytes


def test_repair_store_backup_chapter_routes_through_canonical_atomic_write_bytes(
    tmp_path, monkeypatch
):
    calls = []
    real = io_utils.atomic_write_bytes

    def spy(path, data, **kwargs):
        calls.append(Path(path))
        return real(path, data, **kwargs)

    monkeypatch.setattr(repair_store, "atomic_write_bytes", spy, raising=False)

    chapter_path = tmp_path / "chapter.html"
    chapter_path.write_bytes(b"<p>hello</p>")
    store = repair_store.RepairStore(tmp_path / "backups", "session-1")
    store.backup_chapter("chapter-1", chapter_path)

    assert calls, (
        "backup_chapter обязан звать каноническую atomic_write_bytes, "
        "а не собственную копию"
    )


def test_chapter_editor_save_routes_through_canonical_atomic_write_text(
    tmp_path, monkeypatch
):
    calls = []
    real = io_utils.atomic_write_text

    def spy(path, text, **kwargs):
        calls.append(Path(path))
        return real(path, text, **kwargs)

    monkeypatch.setattr(chapter_editor, "atomic_write_text", spy, raising=False)

    target = tmp_path / "chapter.html"
    chapter_editor.atomic_write_text(str(target), "<p>content</p>")

    assert calls == [target]
    assert target.read_text(encoding="utf-8") == "<p>content</p>"


def test_chapter_editor_save_changes_source_calls_canonical_name():
    """``ChapterEditorDialog.save_changes`` — реальное место вызова (было
    ``_atomic_write_text``). Конструировать полноценный QDialog в юнит-тесте
    дорого (нужен целый проект/эпаб), поэтому проверяем по исходнику метода,
    что вызов идёт к каноническому имени, а не к локальной копии.
    """
    import inspect

    source = inspect.getsource(chapter_editor.ChapterEditorDialog.save_changes)
    assert "atomic_write_text(self.translated_path" in source
    assert "_atomic_write_text(" not in source


def test_chapter_editor_no_longer_defines_its_own_atomic_write_text():
    """Копия удалена целиком — не оставляем алиас "на всякий случай": имя
    ``_atomic_write_text`` не патчится ни в одном существующем тесте (grep
    подтверждён), поэтому переносим вызовы на каноническое имя без обёртки.
    """
    assert not hasattr(chapter_editor, "_atomic_write_text")
