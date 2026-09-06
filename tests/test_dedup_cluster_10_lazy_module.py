"""Характеризационные и routing-тесты для дедупликации cluster-10.

PEP 562 ``__getattr__`` и self-maintenance скрипт (``python __init__.py``)
были продублированы дословно между ``gemini_translator/api/handlers/__init__.py``
и ``gemini_translator/api/servers/__init__.py``. Общая логика вынесена в
``gemini_translator/api/lazy_module.py``; расхождения (servers игнорирует
файл ``base.py`` целиком при сканировании, а также молчит при отсутствующем
SEPARATOR, тогда как handlers печатает сообщение об ошибке) сохранены как
параметры.
"""

import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from gemini_translator.api import lazy_module
from gemini_translator.api import handlers as _handlers_pkg
from gemini_translator.api import servers as _servers_pkg


class LazyAttrTests(unittest.TestCase):
    """lazy_attr — общее тело PEP 562 __getattr__."""

    def test_imports_and_returns_requested_class(self):
        from gemini_translator.api import handlers

        cls = lazy_module.lazy_attr(
            "LocalApiHandler", handlers._LAZY_HANDLER_MODULES, handlers.__name__
        )
        self.assertEqual(cls.__name__, "LocalApiHandler")

    def test_unknown_name_raises_attribute_error(self):
        with self.assertRaises(AttributeError) as ctx:
            lazy_module.lazy_attr("NoSuchThing", {"Known": ".mod"}, "some.pkg")
        self.assertIn("some.pkg", str(ctx.exception))
        self.assertIn("NoSuchThing", str(ctx.exception))


class FindClassesTests(unittest.TestCase):
    """find_classes — общее тело find_handlers/find_servers, включая
    расхождение: servers дополнительно игнорирует весь файл base.py."""

    def _make_pkg(self, tmp):
        (tmp / "__init__.py").write_text("", encoding="utf-8")
        (tmp / "widget.py").write_text(
            "class WidgetApiHandler:\n    pass\n", encoding="utf-8"
        )
        (tmp / "base.py").write_text(
            "class BaseApiHandler:\n    pass\n\n"
            "class JunkApiHandler:\n    pass\n",
            encoding="utf-8",
        )

    def test_default_scans_base_py_too(self):
        import pathlib

        with tempfile.TemporaryDirectory() as d:
            tmp = pathlib.Path(d)
            self._make_pkg(tmp)
            found = lazy_module.find_classes(
                str(tmp), class_suffix="ApiHandler", base_class_name="BaseApiHandler"
            )
        names = {name for _, name in found}
        # BaseApiHandler отфильтрован по имени, но JunkApiHandler из base.py
        # найден — handlers-версия не игнорирует файл base.py целиком.
        self.assertIn("WidgetApiHandler", names)
        self.assertIn("JunkApiHandler", names)
        self.assertNotIn("BaseApiHandler", names)

    def test_ignore_files_skips_whole_file(self):
        import pathlib

        with tempfile.TemporaryDirectory() as d:
            tmp = pathlib.Path(d)
            self._make_pkg(tmp)
            found = lazy_module.find_classes(
                str(tmp),
                class_suffix="ApiHandler",
                base_class_name="BaseApiHandler",
                ignore_files=("base.py",),
            )
        names = {name for _, name in found}
        # servers-версия игнорирует весь base.py — JunkApiHandler не найден.
        self.assertIn("WidgetApiHandler", names)
        self.assertNotIn("JunkApiHandler", names)
        self.assertNotIn("BaseApiHandler", names)


class RegenerateSelfTests(unittest.TestCase):
    """regenerate_self — общее тело self-maintenance генератора."""

    def test_writes_new_lazy_section_and_keeps_script_logic(self):
        tail = (
            f"{lazy_module.SEPARATOR}\n"
            "#  SELF-MAINTENANCE SCRIPT\n"
            f"{lazy_module.SEPARATOR}\n"
            "print('tail kept')\n"
        )
        with tempfile.NamedTemporaryFile(
            "w", suffix=".py", delete=False, encoding="utf-8"
        ) as f:
            f.write("# old header\n" + tail)
            path = f.name
        try:
            lazy_module.regenerate_self(
                current_file=path,
                classes=[("widget", "WidgetApiHandler")],
                registry_name="_LAZY_HANDLER_MODULES",
            )
            with open(path, encoding="utf-8") as f:
                new_content = f.read()
        finally:
            os.unlink(path)

        self.assertIn('"WidgetApiHandler": ".widget",', new_content)
        self.assertIn("_LAZY_HANDLER_MODULES = {", new_content)
        self.assertIn(
            "lazy_module.lazy_attr(name, _LAZY_HANDLER_MODULES, __name__)",
            new_content,
        )
        self.assertIn("print('tail kept')", new_content)

    def test_missing_separator_calls_callback_and_does_not_write(self):
        with tempfile.NamedTemporaryFile(
            "w", suffix=".py", delete=False, encoding="utf-8"
        ) as f:
            f.write("no separator here\n")
            path = f.name
        callback = mock.Mock()
        try:
            lazy_module.regenerate_self(
                current_file=path,
                classes=[],
                registry_name="_LAZY_HANDLER_MODULES",
                on_missing_separator=callback,
            )
            with open(path, encoding="utf-8") as f:
                content_after = f.read()
        finally:
            os.unlink(path)

        callback.assert_called_once()
        self.assertEqual(content_after, "no separator here\n")

    def test_missing_separator_without_callback_is_silent(self):
        # servers-версия: при отсутствии разделителя просто return, без
        # сообщения об ошибке и без какого-либо вывода на stdout.
        with tempfile.NamedTemporaryFile(
            "w", suffix=".py", delete=False, encoding="utf-8"
        ) as f:
            f.write("no separator here\n")
            path = f.name
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                lazy_module.regenerate_self(
                    current_file=path,
                    classes=[],
                    registry_name="_LAZY_SERVER_MODULES",
                )
            with open(path, encoding="utf-8") as f:
                content_after = f.read()
        finally:
            os.unlink(path)
        self.assertEqual(content_after, "no separator here\n")
        self.assertEqual(buf.getvalue(), "")


class GetattrRoutingTests(unittest.TestCase):
    """Оба __init__.py должны звать lazy_module.lazy_attr, а не хранить
    свою копию тела __getattr__. Эти тесты обязаны падать ДО рефакторинга
    (у каждого пакета была своя инлайновая копия) и проходить после."""

    def test_handlers_getattr_routes_through_lazy_module(self):
        from gemini_translator.api import handlers

        name = "GeminiApiHandler"
        previous = handlers.__dict__.pop(name, None)
        sentinel = object()
        try:
            with mock.patch.object(
                lazy_module, "lazy_attr", return_value=sentinel
            ) as stub:
                result = getattr(handlers, name)
            stub.assert_called_once_with(
                name, handlers._LAZY_HANDLER_MODULES, handlers.__name__
            )
            self.assertIs(result, sentinel)
            self.assertIs(handlers.__dict__[name], sentinel)
        finally:
            handlers.__dict__.pop(name, None)
            if previous is not None:
                handlers.__dict__[name] = previous

    def test_servers_getattr_routes_through_lazy_module(self):
        from gemini_translator.api import servers

        name = "PerplexityServer"
        previous = servers.__dict__.pop(name, None)
        sentinel = object()
        try:
            with mock.patch.object(
                lazy_module, "lazy_attr", return_value=sentinel
            ) as stub:
                result = getattr(servers, name)
            stub.assert_called_once_with(
                name, servers._LAZY_SERVER_MODULES, servers.__name__
            )
            self.assertIs(result, sentinel)
            self.assertIs(servers.__dict__[name], sentinel)
        finally:
            servers.__dict__.pop(name, None)
            if previous is not None:
                servers.__dict__[name] = previous


class SelfMaintenanceScriptExecutionTests(unittest.TestCase):
    """Характеризационные тесты для регрессии из code-review cluster-10:
    ``python __init__.py`` должен по-прежнему работать при запуске напрямую
    из своей директории (как предписывает автогенерируемый заголовок файла и
    как работало до выноса общей логики в lazy_module.py), а не падать с
    ``ModuleNotFoundError: No module named 'gemini_translator'`` из-за того,
    что ``sys.path[0]`` при запуске скрипта — это сама папка handlers/servers,
    а не корень репозитория, и пакет ``gemini_translator`` не установлен как
    дистрибутив в окружении.

    Тесты выполняют реальный ``__init__.py`` (без изменений) в изолированном
    fake-дереве пакетов, скопировав в него текущий ``lazy_module.py`` и
    проверяемый ``__init__.py`` — рабочее дерево репозитория не трогается.
    """

    def _build_fake_tree(self, tmp_root, init_source_path):
        # tmp_root/gemini_translator/api/{handlers,servers}/__init__.py
        pkg_root = os.path.join(tmp_root, "gemini_translator")
        api_root = os.path.join(pkg_root, "api")
        subpkg_name = os.path.basename(os.path.dirname(init_source_path))
        subpkg_dir = os.path.join(api_root, subpkg_name)
        os.makedirs(subpkg_dir)

        open(os.path.join(pkg_root, "__init__.py"), "w", encoding="utf-8").close()
        open(os.path.join(api_root, "__init__.py"), "w", encoding="utf-8").close()
        shutil.copyfile(
            os.path.join(os.path.dirname(lazy_module.__file__), "lazy_module.py"),
            os.path.join(api_root, "lazy_module.py"),
        )
        shutil.copyfile(init_source_path, os.path.join(subpkg_dir, "__init__.py"))
        return subpkg_dir

    def _run_as_script(self, real_init_path):
        with tempfile.TemporaryDirectory() as tmp_root:
            subpkg_dir = self._build_fake_tree(tmp_root, real_init_path)
            proc = subprocess.run(
                [sys.executable, "__init__.py"],
                cwd=subpkg_dir,
                capture_output=True,
                text=True,
                timeout=30,
            )
        return proc

    def test_handlers_init_runs_as_script_from_its_own_directory(self):
        proc = self._run_as_script(_handlers_pkg.__file__)
        self.assertNotIn(
            "ModuleNotFoundError",
            proc.stderr,
            msg=f"stdout={proc.stdout!r} stderr={proc.stderr!r}",
        )
        self.assertEqual(
            0, proc.returncode, msg=f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
        )
        self.assertIn("успешно обновлен", proc.stdout)

    def test_servers_init_runs_as_script_from_its_own_directory(self):
        proc = self._run_as_script(_servers_pkg.__file__)
        self.assertNotIn(
            "ModuleNotFoundError",
            proc.stderr,
            msg=f"stdout={proc.stdout!r} stderr={proc.stderr!r}",
        )
        self.assertEqual(
            0, proc.returncode, msg=f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
        )
        self.assertIn("успешно обновлен", proc.stdout)


if __name__ == "__main__":
    unittest.main()
