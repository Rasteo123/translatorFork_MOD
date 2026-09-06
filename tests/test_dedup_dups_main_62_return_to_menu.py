# -*- coding: utf-8 -*-
"""dups-main-62 / root-entry/design/8-return-to-menu-duplicate-closu:

`return_to_menu` (закрытие текущего инструмента и выход из QApplication.exec()
с кодом EXIT_CODE_REBOOT, чтобы главный цикл main.py пересоздал MainShell) была
побитово продублирована как вложенная функция в build_ranobelib_window() и в
build_gemini_reader_window() (main.py).

Каноническая реализация - фабрика `main._make_return_to_menu_handler()`,
переиспользуемая в обоих местах.

(a) Характеризационные тесты фиксируют поведение фабрики: обработчик зовёт
    QApplication.instance().exit(EXIT_CODE_REBOOT), если инстанс есть, и ничего
    не делает (без исключения), если QApplication ещё/уже не существует.
(b) Тест-маршрутизация проверяет, что оба build_*_window() реально используют
    main._make_return_to_menu_handler(), а не свою локальную копию - эти тесты
    обязаны падать до рефакторинга и проходить после.
"""

import os
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

# main импортируется на уровне модуля, до любых monkeypatch (см. пояснение в
# test_dedup_cluster_02_playwright_runtime.py) - main.py на импорте определяет
# класс, наследующий QtWidgets.QApplication.
import main

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


# ---------------------------------------------------------------------------
# (a) Характеризационные тесты main._make_return_to_menu_handler
# ---------------------------------------------------------------------------


def test_handler_exits_running_application_with_reboot_code(monkeypatch):
    # Патчим exit() у РЕАЛЬНОГО инстанса QApplication, а не сам instance() -
    # pytest-qt дергает QApplication.instance().processEvents() в своём
    # хуке очистки после каждого теста, и подмена instance() на объект без
    # этого метода валит тест на этапе teardown, а не assert.
    calls = []
    monkeypatch.setattr(_APP, "exit", lambda code: calls.append(code))

    handler = main._make_return_to_menu_handler()
    handler()

    assert calls == [main.EXIT_CODE_REBOOT]


def test_handler_is_noop_without_running_application(monkeypatch):
    monkeypatch.setattr(QtWidgets.QApplication, "instance", staticmethod(lambda: None))

    handler = main._make_return_to_menu_handler()
    handler()  # не должно бросать исключение


# ---------------------------------------------------------------------------
# (b) Тест-маршрутизация: build_ranobelib_window / build_gemini_reader_window
# ---------------------------------------------------------------------------


class _HandlerCapturingWindow:
    def __init__(self):
        self.handler = "unset"

    def set_return_to_menu_handler(self, handler):
        self.handler = handler


def test_build_ranobelib_window_routes_return_to_menu_through_factory(tmp_path, monkeypatch):
    sentinel_handler = lambda: None  # noqa: E731

    monkeypatch.setattr(main, "_make_return_to_menu_handler", lambda: sentinel_handler)
    monkeypatch.setattr(main.api_config, "configure_playwright_runtime", lambda: None)
    monkeypatch.setattr(main, "resolve_ranobelib_source_dir", lambda: (tmp_path, []))
    monkeypatch.setattr(main, "patch_ranobelib_login_worker", lambda: None)

    captured_window = _HandlerCapturingWindow()
    fake_main_window_module = SimpleNamespace(
        RanobeUploaderApp=lambda: captured_window
    )

    class _FakeImportlib:
        @staticmethod
        def invalidate_caches():
            pass

        @staticmethod
        def import_module(name):
            assert name == "main_window"
            return fake_main_window_module

    monkeypatch.setattr(main, "importlib", _FakeImportlib)
    monkeypatch.setattr(sys, "path", list(sys.path))

    modules_snapshot = {
        name: sys.modules.get(name) for name in main.RANOBELIB_MODULE_NAMES
    }
    try:
        window = main.build_ranobelib_window()
    finally:
        for name, module in modules_snapshot.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    assert window is captured_window
    assert window.handler is sentinel_handler, (
        "build_ranobelib_window обязан ставить обработчик, полученный от "
        "main._make_return_to_menu_handler(), а не свою локальную копию return_to_menu"
    )


def test_build_gemini_reader_window_routes_return_to_menu_through_factory(monkeypatch):
    sentinel_handler = lambda: None  # noqa: E731

    monkeypatch.setattr(main, "_make_return_to_menu_handler", lambda: sentinel_handler)

    captured_window = _HandlerCapturingWindow()
    fake_gemini_reader_v3 = SimpleNamespace(MainWindow=lambda: captured_window)
    monkeypatch.setitem(sys.modules, "gemini_reader_v3", fake_gemini_reader_v3)

    window = main.build_gemini_reader_window()

    assert window is captured_window
    assert window.handler is sentinel_handler, (
        "build_gemini_reader_window обязан ставить обработчик, полученный от "
        "main._make_return_to_menu_handler(), а не свою локальную копию return_to_menu"
    )
