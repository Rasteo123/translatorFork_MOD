# -*- coding: utf-8 -*-
"""
Тест-маршрутизация для libs-fs-memfs: os_patch.py не должен зависеть от
стороннего пакета `fs` (PyFilesystem2) — ни для реализации mem:// (MemoryFS),
ни для прокси os.path (fs.path). До замены этот тест закономерно падает
(RED) — os_patch.py делает `import fs` и `from fs import path as fs_path`.
После замены собственным MiniMemFS + posixpath-диспетчером тест зелёный.
"""
import ast
import inspect

import os_patch


def _imported_top_level_module_names(source: str) -> set[str]:
    tree = ast.parse(source)
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module.split(".")[0])
    return names


def test_os_patch_source_does_not_import_the_fs_package():
    source = inspect.getsource(os_patch)
    imported = _imported_top_level_module_names(source)
    assert "fs" not in imported, (
        "os_patch.py всё ещё импортирует пакет `fs` (PyFilesystem2); "
        "контракт mem:// должен обслуживаться собственным MiniMemFS."
    )


def test_os_patch_module_namespace_has_no_fs_or_fs_path_bindings():
    assert not hasattr(os_patch, "fs"), "os_patch.fs всё ещё привязан к пакету fs"
    assert not hasattr(os_patch, "fs_path"), "os_patch.fs_path всё ещё привязан к fs.path"


def test_mem_fs_factory_does_not_return_a_pyfilesystem2_instance():
    from PyQt6 import QtWidgets

    # Чужой (предсуществующий) app.mem_fs — общий объект: откладываем в
    # сторону без close() и возвращаем как есть; close() только для фс,
    # созданной внутри этого теста (см. ту же конвенцию в
    # tests/test_libs_libs_fs_memfs_characterization.py::_fresh_mem_fs).
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    had_attr = hasattr(app, "mem_fs")
    old_value = getattr(app, "mem_fs", None)
    if had_attr:
        delattr(app, "mem_fs")

    try:
        mem_fs = os_patch._get_or_create_mem_fs()
        module_name = type(mem_fs).__module__
        assert not module_name.startswith("fs."), (
            f"_get_or_create_mem_fs() вернул экземпляр {module_name}."
            f"{type(mem_fs).__name__} — это всё ещё PyFilesystem2, а не "
            "собственный MiniMemFS"
        )
    finally:
        current = getattr(app, "mem_fs", None)
        if current is not None and current is not old_value:
            try:
                current.close()
            except Exception:
                pass
            delattr(app, "mem_fs")
        if had_attr:
            app.mem_fs = old_value
