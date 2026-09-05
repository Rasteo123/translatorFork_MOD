# -*- coding: utf-8 -*-
"""
Регресс на root-entry/bugs/1-os-patch-replace-not-atomic.

os_patch._patched_replace для реальных (не mem://) путей раскладывал атомарный
os.replace() на два отдельных системных вызова: _patched_remove(dst) и
_patched_rename(src, dst). Между ними существует окно, когда dst уже удалён,
а src ещё не переименован — если процесс падает или rename не проходит именно
в этот момент, файл состояния (settings.json, job.json и т.п.) теряется
безвозвратно, хотя настоящий os.replace гарантирует "либо старое, либо новое".

Тесты бьют прямо в тело _patched_replace на временных файлах, без вызова
os_patch.apply() (чтобы не трогать глобальный os модуль в общем дереве).
"""

import os_patch


def test_patched_replace_missing_src_leaves_dst_intact(tmp_path):
    """Настоящий os.replace(missing_src, dst) не трогает dst и просто
    кидает FileNotFoundError. Сломанная версия сначала удаляла dst
    (_patched_remove), а уже потом обнаруживала, что src не существует —
    в итоге dst терялся безвозвратно."""
    dst = tmp_path / "important.json"
    dst.write_text('{"v": "OLD-VALUABLE"}', encoding="utf-8")
    missing_src = tmp_path / "does_not_exist.tmp"

    raised = None
    try:
        os_patch._patched_replace(str(missing_src), str(dst))
    except OSError as exc:
        raised = exc

    assert raised is not None, "ожидалась OSError из-за отсутствующего src"
    assert dst.exists(), "dst не должен исчезать, если replace не смог применить src"
    assert dst.read_text(encoding="utf-8") == '{"v": "OLD-VALUABLE"}'


def test_patched_replace_existing_dst_uses_single_native_call(tmp_path, monkeypatch):
    """Замена существующего dst не должна проходить через отдельный
    _original["remove"](dst) — это и есть источник неатомарности (окно между
    remove и rename). Правильная реализация делает одну нативную замену."""
    src = tmp_path / "new.json"
    dst = tmp_path / "settings.json"
    src.write_text('{"v": "NEW"}', encoding="utf-8")
    dst.write_text('{"v": "OLD"}', encoding="utf-8")

    remove_calls = []
    orig_remove = os_patch._original["remove"]

    def spy_remove(path):
        remove_calls.append(path)
        return orig_remove(path)

    monkeypatch.setitem(os_patch._original, "remove", spy_remove)

    os_patch._patched_replace(str(src), str(dst))

    assert remove_calls == [], (
        "_patched_replace не должен отдельно удалять dst перед переименованием "
        f"(зафиксированы вызовы remove: {remove_calls})"
    )
    assert not src.exists()
    assert dst.read_text(encoding="utf-8") == '{"v": "NEW"}'
