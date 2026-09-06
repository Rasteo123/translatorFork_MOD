# -*- coding: utf-8 -*-
"""
Характеризационные тесты для контракта mem:// в os_patch.py (libs-fs-memfs).

Фиксируют НАБЛЮДАЕМОЕ поведение публичного API os_patch (copy_to_mem,
write_bytes_to_mem, copy_from_mem, HybridPath.join/exists/isdir/isfile) через
объект, который возвращает os_patch._get_or_create_mem_fs() — не через прямой
`import fs`. Поэтому эти тесты одинаково зелёные и на старой реализации
(fs.memoryfs.MemoryFS), и на новой (собственный MiniMemFS): они фиксируют
контракт, а не конкретную библиотеку. RED здесь невозможен по построению —
это снимок уже работающего поведения.
"""
import os as _os
import zipfile
from io import BytesIO

import pytest

_os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

import os_patch


@pytest.fixture(autouse=True)
def _fresh_mem_fs(monkeypatch):
    """Каждый тест получает свежую, независимую mem-фс через официальный API
    os_patch (какой бы класс он ни возвращал).

    ВАЖНО: чужой (предсуществующий) app.mem_fs — общий объект, который может
    использовать другой тестовый модуль в этом же процессе. Мы его никогда не
    закрываем и не опустошаем — только откладываем в сторону на время теста
    и возвращаем на место как есть. close() вызывается только для фс,
    созданной _внутри_ этого теста."""
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    # _get_or_create_mem_fs кэширует созданную фс на QApplication.instance();
    # сбрасываем кэш перед тестом, чтобы os_patch создал новый изолированный
    # объект той же фабрикой, что использует сам модуль в проде.
    had_attr = hasattr(app, "mem_fs")
    old_value = getattr(app, "mem_fs", None)
    if had_attr:
        delattr(app, "mem_fs")
    yield
    current = getattr(app, "mem_fs", None)
    if current is not None and current is not old_value:
        try:
            current.close()
        except Exception:
            pass
        delattr(app, "mem_fs")
    if had_attr:
        app.mem_fs = old_value


def test_get_or_create_mem_fs_is_memoized_on_the_app():
    fs1 = os_patch._get_or_create_mem_fs()
    fs2 = os_patch._get_or_create_mem_fs()
    assert fs1 is fs2


def test_mem_fs_basic_contract_exists_isdir_isfile_openbin_writebytes_remove():
    mem_fs = os_patch._get_or_create_mem_fs()

    mem_fs.makedirs("/dir/sub", recreate=True)
    assert mem_fs.isdir("/dir/sub")
    assert not mem_fs.isfile("/dir/sub")

    mem_fs.writebytes("/dir/sub/file.bin", b"hello-bytes")
    assert mem_fs.exists("/dir/sub/file.bin")
    assert mem_fs.isfile("/dir/sub/file.bin")
    assert not mem_fs.isdir("/dir/sub/file.bin")

    with mem_fs.openbin("/dir/sub/file.bin") as handle:
        assert handle.read() == b"hello-bytes"

    mem_fs.remove("/dir/sub/file.bin")
    assert not mem_fs.exists("/dir/sub/file.bin")


def test_mem_fs_getinfo_size_matches_written_bytes():
    mem_fs = os_patch._get_or_create_mem_fs()
    mem_fs.writebytes("/sized.bin", b"0123456789")
    info = mem_fs.getinfo("/sized.bin", namespaces=["details"])
    assert info.size == 10


def test_mem_fs_listdir_reports_direct_children_only():
    mem_fs = os_patch._get_or_create_mem_fs()
    mem_fs.makedirs("/root/child", recreate=True)
    mem_fs.writebytes("/root/a.txt", b"a")
    mem_fs.writebytes("/root/child/b.txt", b"b")

    names = sorted(mem_fs.listdir("/root"))
    assert names == ["a.txt", "child"]


def test_mem_fs_move_relocates_bytes_and_removes_source():
    # ВАЖНО (зафиксировано наблюдением за fs.memoryfs.MemoryFS.move):
    # move() НЕ создаёт директорию назначения неявно — если родительская
    # директория dst не существует, поднимается ResourceNotFound. Вызывающий
    # код (_patched_rename) полагается именно на это поведение.
    mem_fs = os_patch._get_or_create_mem_fs()
    mem_fs.writebytes("/src.bin", b"payload")
    mem_fs.makedirs("/dst", recreate=True)

    mem_fs.move("/src.bin", "/dst/dst.bin")

    assert not mem_fs.exists("/src.bin")
    assert mem_fs.exists("/dst/dst.bin")
    with mem_fs.openbin("/dst/dst.bin") as handle:
        assert handle.read() == b"payload"


def test_mem_fs_move_to_missing_parent_dir_raises():
    mem_fs = os_patch._get_or_create_mem_fs()
    mem_fs.writebytes("/src2.bin", b"payload")

    with pytest.raises(Exception):
        mem_fs.move("/src2.bin", "/no/such/dir/dst.bin")


def test_hybrid_path_join_on_virtual_path_returns_prefixed_result():
    hybrid_path = os_patch.HybridPath()
    joined = hybrid_path.join("mem://session-cache", "chapter-1")
    assert joined == "mem://session-cache/chapter-1"


def test_hybrid_path_forwards_real_paths_to_native_os_path():
    hybrid_path = os_patch.HybridPath()
    assert hybrid_path.join("/a", "b") == _os.path.join("/a", "b")
    assert hybrid_path.exists("/definitely/not/a/real/path") is False
    assert hybrid_path.sep == _os.sep


def test_hybrid_path_exists_isfile_isdir_route_to_mem_fs_for_virtual_paths():
    # Тестируем HybridPath напрямую (без os_patch.apply()), чтобы не трогать
    # глобальный os.path в общем тестовом процессе — см. конвенцию в
    # tests/test_fix_g35_os_patch_replace_atomicity.py.
    hybrid_path = os_patch.HybridPath()
    written = os_patch.write_bytes_to_mem(b"hello", ".txt")
    mem_fs = os_patch._get_or_create_mem_fs()
    mem_fs.makedirs("/tree/leaf", recreate=True)
    directory = hybrid_path.join("mem://tree", "leaf")

    assert hybrid_path.exists(written)
    assert hybrid_path.isfile(written)
    assert not hybrid_path.isdir(written)

    assert hybrid_path.exists(directory)
    assert hybrid_path.isdir(directory)
    assert not hybrid_path.isfile(directory)


def test_write_bytes_to_mem_round_trips_through_patched_open(monkeypatch):
    monkeypatch.setattr(_os, "write_bytes_to_mem", os_patch.write_bytes_to_mem, raising=False)
    virtual_path = os_patch.write_bytes_to_mem(b"payload-bytes", ".bin")
    assert virtual_path.startswith("mem://")

    with os_patch._patched_open(virtual_path, "rb") as handle:
        assert handle.read() == b"payload-bytes"


def test_patched_open_missing_mem_file_raises_file_not_found_error():
    with pytest.raises(FileNotFoundError):
        os_patch._patched_open("mem://does/not/exist.bin", "rb")


def test_mem_fs_open_text_mode_round_trips_with_encoding():
    mem_fs = os_patch._get_or_create_mem_fs()
    with mem_fs.open("/note.txt", "w", encoding="utf-8") as handle:
        handle.write("привет мир")

    with mem_fs.open("/note.txt", "r", encoding="utf-8") as handle:
        assert handle.read() == "привет мир"

    with mem_fs.openbin("/note.txt") as handle:
        assert handle.read() == "привет мир".encode("utf-8")


def test_copy_to_mem_and_copy_from_mem_round_trip_bytes(tmp_path):
    source = tmp_path / "book.epub"
    source.write_bytes(b"epub-bytes-payload")

    virtual_path = os_patch.copy_to_mem(str(source))
    assert virtual_path is not None
    assert virtual_path.startswith("mem://")

    dest = tmp_path / "restored.epub"
    assert os_patch.copy_from_mem(virtual_path, str(dest)) is True
    assert dest.read_bytes() == b"epub-bytes-payload"


def test_copy_to_mem_unique_produces_isolated_independent_copies(tmp_path):
    source = tmp_path / "book.epub"
    source.write_bytes(b"epub-data")

    queue_path = os_patch.copy_to_mem(str(source))
    dialog_path = os_patch.copy_to_mem(str(source), unique=True)

    assert queue_path != dialog_path

    mem_fs = os_patch._get_or_create_mem_fs()
    mem_fs.remove(dialog_path.removeprefix("mem://"))

    assert mem_fs.exists(queue_path.removeprefix("mem://"))
    with mem_fs.openbin(queue_path.removeprefix("mem://")) as handle:
        assert handle.read() == b"epub-data"


def _epub_bytes(chapter_count):
    output = BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for chapter_number in range(1, chapter_count + 1):
            archive.writestr(
                f"OEBPS/chapter_{chapter_number}.xhtml",
                f"<html><body>Chapter {chapter_number}</body></html>",
            )
    return output.getvalue()


def test_copy_to_mem_refreshes_when_source_file_changes_at_same_path(tmp_path):
    source = tmp_path / "book.epub"
    source.write_bytes(_epub_bytes(3))

    queue_path = os_patch.copy_to_mem(str(source))
    source.write_bytes(_epub_bytes(4))

    refreshed_path = os_patch.copy_to_mem(str(source))
    assert refreshed_path == queue_path

    mem_fs = os_patch._get_or_create_mem_fs()
    with mem_fs.openbin(queue_path.removeprefix("mem://")) as handle:
        with zipfile.ZipFile(handle) as archive:
            assert "OEBPS/chapter_4.xhtml" in archive.namelist()


def test_writing_a_new_version_is_visible_after_closing_a_stale_read_handle():
    # РЕВЬЮ (major #1a): хендл, открытый на чтение ДО того, как писатель
    # перезаписал тот же путь, не должен при своём close() откатывать файл
    # обратно к старому содержимому. У fs.MemoryFS все хендлы одного пути
    # делят один и тот же буфер — close() ничего не пишет обратно.
    mem_fs = os_patch._get_or_create_mem_fs()
    mem_fs.writebytes("/a.bin", b"OLD")

    reader = mem_fs.openbin("/a.bin")
    try:
        mem_fs.writebytes("/a.bin", b"NEW")
    finally:
        reader.close()

    with mem_fs.openbin("/a.bin") as handle:
        assert handle.read() == b"NEW"


def test_closing_a_stale_read_handle_does_not_resurrect_a_removed_file():
    # РЕВЬЮ (major #1b): remove() удалённого файла не должен "воскресать"
    # из-за close() хендла, открытого на чтение до удаления.
    mem_fs = os_patch._get_or_create_mem_fs()
    mem_fs.writebytes("/gone.bin", b"payload")

    reader = mem_fs.openbin("/gone.bin")
    try:
        mem_fs.remove("/gone.bin")
    finally:
        reader.close()

    assert not mem_fs.exists("/gone.bin")


def test_file_exists_immediately_once_opened_for_writing():
    # РЕВЬЮ (major #2): fs.MemoryFS регистрирует запись в каталоге СРАЗУ при
    # openbin(..., 'w'), до какой-либо записи байт и до close(). Проверка
    # "пишем и тут же os.path.exists(...)" должна видеть True немедленно.
    mem_fs = os_patch._get_or_create_mem_fs()
    handle = mem_fs.openbin("/new_while_open.bin", "w")
    try:
        assert mem_fs.exists("/new_while_open.bin")
        assert mem_fs.isfile("/new_while_open.bin")
    finally:
        handle.close()


def test_two_concurrent_write_handles_share_one_live_buffer():
    # РЕВЬЮ (major #2): два одновременно открытых write-хендла на один путь
    # у fs.MemoryFS делят ОДИН и тот же буфер (запись через один хендл видна
    # другому), а не независимые снимки "победил закрывшийся последним".
    mem_fs = os_patch._get_or_create_mem_fs()
    mem_fs.writebytes("/shared.bin", b"")

    handle_a = mem_fs.openbin("/shared.bin", "r+")
    handle_b = mem_fs.openbin("/shared.bin", "r+")
    try:
        handle_a.write(b"A")
        handle_a.flush()
        handle_b.seek(0)
        assert handle_b.read() == b"A"
    finally:
        handle_a.close()
        handle_b.close()

    with mem_fs.openbin("/shared.bin") as handle:
        assert handle.read() == b"A"


def test_mem_fs_open_text_mode_round_trips_crlf_bytes_unchanged():
    # РЕВЬЮ (minor #3): fs.base.open() использует newline="" — переводы
    # строк не транслируются. Виндовый CI это важно: с newline=None (по
    # умолчанию у io.TextIOWrapper) '\r\n' на чтении схлопнулся бы в '\n', а
    # на запись — универсальный перевод строк подставил бы os.linesep.
    mem_fs = os_patch._get_or_create_mem_fs()
    mem_fs.writebytes("/crlf.txt", b"a\r\nb")

    with mem_fs.open("/crlf.txt", "r", encoding="utf-8") as handle:
        assert handle.read() == "a\r\nb"

    with mem_fs.open("/crlf.txt", "w", encoding="utf-8") as handle:
        handle.write("x\r\ny")

    with mem_fs.openbin("/crlf.txt") as handle:
        assert handle.read() == b"x\r\ny"


def test_writebytes_to_missing_parent_dir_raises_like_fs_memoryfs():
    # РЕВЬЮ (minor #4): fs.MemoryFS.writebytes на отсутствующем родителе
    # поднимает ResourceNotFound, а не создаёт директорию неявно.
    mem_fs = os_patch._get_or_create_mem_fs()
    with pytest.raises(os_patch.MemFSResourceNotFound):
        mem_fs.writebytes("/no/such/parent/file.bin", b"data")


def test_open_for_write_on_missing_parent_dir_raises_like_fs_memoryfs():
    mem_fs = os_patch._get_or_create_mem_fs()
    with pytest.raises(os_patch.MemFSResourceNotFound):
        mem_fs.openbin("/no/such/parent/file2.bin", "w")


def test_operations_after_close_raise_instead_of_silently_running_empty():
    # РЕВЬЮ (minor): _closed выставлялся, но нигде не проверялся — после
    # close() фс продолжала молча работать как пустая. fs поднимает
    # FilesystemClosed; здесь — наш аналог MemFSClosedError.
    mem_fs = os_patch.MiniMemFS()
    mem_fs.writebytes("/x.bin", b"data")
    mem_fs.close()

    with pytest.raises(os_patch.MemFSClosedError):
        mem_fs.exists("/x.bin")
    with pytest.raises(os_patch.MemFSClosedError):
        mem_fs.writebytes("/y.bin", b"data")


def test_zipfile_can_read_from_mem_path_transparently(tmp_path):
    source = tmp_path / "book.epub"
    source.write_bytes(_epub_bytes(2))

    virtual_path = os_patch.copy_to_mem(str(source))

    with os_patch._patched_open(virtual_path, "rb") as handle:
        with zipfile.ZipFile(handle) as archive:
            names = archive.namelist()

    assert names == ["OEBPS/chapter_1.xhtml", "OEBPS/chapter_2.xhtml"]
