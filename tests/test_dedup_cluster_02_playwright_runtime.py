# -*- coding: utf-8 -*-
"""cluster-02: настройка Playwright-рантайма (Windows event loop policy +
переменные окружения PLAYWRIGHT_BROWSERS_PATH/PLAYWRIGHT_NODEJS_PATH/
PLAYWRIGHT_PACKAGE_ROOT) была продублирована байт-в-байт между
qidian_rulate/workers.py (`configure_playwright_runtime`, 6 мест вызова) и
main.py (`configure_ranobelib_playwright_runtime`, 1 место вызова).

Оба места уже импортировали один и тот же `gemini_translator.api.config as
api_config`, поэтому канонической реализацией стала
`api_config.configure_playwright_runtime()`.

(a) Характеризационные тесты фиксируют поведение канонической реализации:
    - какие переменные окружения выставляются и когда;
    - Windows-специфичная политика event loop не трогается на прочих ОС и не
      переустанавливается, если уже стоит нужный тип.
(b) Тест-маршрутизация проверяет, что КАЖДОЕ бывшее место вызова (main.py и
    все 6 точек в qidian_rulate/workers.py) реально идёт через
    api_config.configure_playwright_runtime(), а не через свою копию -
    эти тесты обязаны падать до рефакторинга и проходить после.
"""

import asyncio
import os
import sys
from types import SimpleNamespace

import playwright.sync_api as _real_playwright_sync_api
import pytest
from PyQt6 import QtWidgets

from gemini_translator.api import config as api_config

# main импортируется на уровне модуля, ДО любых monkeypatch: main.py на
# импорте определяет `class ApplicationWithContext(QtWidgets.QApplication)`,
# и если QtWidgets.QApplication к этому моменту подменён - наследование
# падает с TypeError.
import main

from qidian_rulate import workers as qidian_workers

# QThread - QObject; на некоторых платформах создание QObject без живого
# QApplication падает. Держим ссылку на уровне модуля, чтобы её не собрал GC
# между тестами.
_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class _FakeWindowsProactorEventLoopPolicy:
    """Заглушка вместо реального asyncio.WindowsProactorEventLoopPolicy -
    на macOS/Linux такого класса в asyncio нет."""


# ---------------------------------------------------------------------------
# (a) Характеризационные тесты gemini_translator.api.config.configure_playwright_runtime
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_playwright_env():
    # configure_playwright_runtime() пишет в os.environ напрямую (не через
    # monkeypatch.setenv), поэтому monkeypatch сам это не откатит - нужно
    # явно сохранить и восстановить исходное состояние вручную, иначе
    # PLAYWRIGHT_NODEJS_PATH/... "утекут" в соседние тесты (в т.ч. те, что
    # реально запускают Playwright/subprocess и споткнутся об наш tmp_path).
    names = ("PLAYWRIGHT_BROWSERS_PATH", "PLAYWRIGHT_NODEJS_PATH", "PLAYWRIGHT_PACKAGE_ROOT")
    original = {name: os.environ.get(name) for name in names}
    for name in names:
        os.environ.pop(name, None)
    try:
        yield
    finally:
        for name in names:
            if original[name] is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = original[name]


def test_sets_env_vars_for_existing_resolved_paths(tmp_path, monkeypatch):
    browsers_dir = tmp_path / "ms-playwright"
    node_path = tmp_path / "node.exe"
    package_dir = tmp_path / "package"
    browsers_dir.mkdir()
    node_path.write_text("", encoding="utf-8")
    package_dir.mkdir()

    monkeypatch.setattr(api_config, "find_playwright_browsers_path", lambda: browsers_dir)
    monkeypatch.setattr(api_config, "find_node_executable", lambda: node_path)
    monkeypatch.setattr(api_config, "find_playwright_package_root", lambda: package_dir)
    monkeypatch.setattr(sys, "platform", "darwin")

    api_config.configure_playwright_runtime()

    assert os.environ["PLAYWRIGHT_BROWSERS_PATH"] == str(browsers_dir)
    assert os.environ["PLAYWRIGHT_NODEJS_PATH"] == str(node_path)
    assert os.environ["PLAYWRIGHT_PACKAGE_ROOT"] == str(package_dir)


def test_skips_env_var_when_resolved_path_does_not_exist(tmp_path, monkeypatch):
    missing = tmp_path / "does-not-exist"

    monkeypatch.setattr(api_config, "find_playwright_browsers_path", lambda: missing)
    monkeypatch.setattr(api_config, "find_node_executable", lambda: None)
    monkeypatch.setattr(api_config, "find_playwright_package_root", lambda: None)
    monkeypatch.setattr(sys, "platform", "darwin")

    api_config.configure_playwright_runtime()

    assert "PLAYWRIGHT_BROWSERS_PATH" not in os.environ
    assert "PLAYWRIGHT_NODEJS_PATH" not in os.environ
    assert "PLAYWRIGHT_PACKAGE_ROOT" not in os.environ


def test_skips_env_var_when_resolved_path_is_falsy(monkeypatch):
    monkeypatch.setattr(api_config, "find_playwright_browsers_path", lambda: None)
    monkeypatch.setattr(api_config, "find_node_executable", lambda: "")
    monkeypatch.setattr(api_config, "find_playwright_package_root", lambda: None)
    monkeypatch.setattr(sys, "platform", "darwin")

    api_config.configure_playwright_runtime()

    assert "PLAYWRIGHT_BROWSERS_PATH" not in os.environ
    assert "PLAYWRIGHT_NODEJS_PATH" not in os.environ
    assert "PLAYWRIGHT_PACKAGE_ROOT" not in os.environ


def test_windows_sets_proactor_policy_when_not_already_set(monkeypatch):
    monkeypatch.setattr(api_config, "find_playwright_browsers_path", lambda: None)
    monkeypatch.setattr(api_config, "find_node_executable", lambda: None)
    monkeypatch.setattr(api_config, "find_playwright_package_root", lambda: None)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        asyncio, "WindowsProactorEventLoopPolicy", _FakeWindowsProactorEventLoopPolicy, raising=False
    )
    monkeypatch.setattr(asyncio, "get_event_loop_policy", lambda: asyncio.DefaultEventLoopPolicy())

    calls = []
    monkeypatch.setattr(asyncio, "set_event_loop_policy", lambda policy: calls.append(policy))

    api_config.configure_playwright_runtime()

    assert len(calls) == 1
    assert isinstance(calls[0], _FakeWindowsProactorEventLoopPolicy)


def test_windows_does_not_reset_policy_when_already_proactor(monkeypatch):
    monkeypatch.setattr(api_config, "find_playwright_browsers_path", lambda: None)
    monkeypatch.setattr(api_config, "find_node_executable", lambda: None)
    monkeypatch.setattr(api_config, "find_playwright_package_root", lambda: None)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        asyncio, "WindowsProactorEventLoopPolicy", _FakeWindowsProactorEventLoopPolicy, raising=False
    )
    monkeypatch.setattr(asyncio, "get_event_loop_policy", lambda: _FakeWindowsProactorEventLoopPolicy())

    calls = []
    monkeypatch.setattr(asyncio, "set_event_loop_policy", lambda policy: calls.append(policy))

    api_config.configure_playwright_runtime()

    assert calls == []


def test_non_windows_never_touches_event_loop_policy(monkeypatch):
    monkeypatch.setattr(api_config, "find_playwright_browsers_path", lambda: None)
    monkeypatch.setattr(api_config, "find_node_executable", lambda: None)
    monkeypatch.setattr(api_config, "find_playwright_package_root", lambda: None)
    monkeypatch.setattr(sys, "platform", "darwin")

    calls = []
    monkeypatch.setattr(asyncio, "set_event_loop_policy", lambda policy: calls.append(policy))

    api_config.configure_playwright_runtime()

    assert calls == []


# ---------------------------------------------------------------------------
# (b) Тест-маршрутизация: main.py
# ---------------------------------------------------------------------------


def test_main_build_ranobelib_window_routes_through_api_config(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(main.api_config, "configure_playwright_runtime", lambda: calls.append(True))
    monkeypatch.setattr(main, "resolve_ranobelib_source_dir", lambda: (tmp_path, []))
    monkeypatch.setattr(main, "patch_ranobelib_login_worker", lambda: None)

    class _FakeRanobeUploaderApp:
        pass

    fake_main_window_module = SimpleNamespace(RanobeUploaderApp=_FakeRanobeUploaderApp)

    class _FakeImportlib:
        @staticmethod
        def invalidate_caches():
            pass

        @staticmethod
        def import_module(name):
            assert name == "main_window", f"неожиданный importlib.import_module({name!r})"
            return fake_main_window_module

    monkeypatch.setattr(main, "importlib", _FakeImportlib)

    # build_ranobelib_window() по-настоящему переставляет sys.path и выбрасывает
    # плоские модули ranobelib (workers, api_upload, main_window, ...) из
    # sys.modules, чтобы импортировать их заново. В тестовом процессе эти
    # модули уже импортированы другими тестами (tests/test_ranobelib_*.py
    # держат ссылки на объекты модулей и monkeypatch'ат их атрибуты), поэтому
    # без восстановления снимка следующий `from workers import ...` получит
    # НОВЫЙ объект модуля, и чужие monkeypatch'и молча перестанут действовать.
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

    assert isinstance(window, _FakeRanobeUploaderApp)
    assert calls == [True], (
        "build_ranobelib_window обязан настраивать Playwright-рантайм через "
        "api_config.configure_playwright_runtime(), а не через свою копию"
    )


# ---------------------------------------------------------------------------
# (b) Тест-маршрутизация: все 6 мест вызова в qidian_rulate/workers.py
# ---------------------------------------------------------------------------


_SENTINEL_TEXT = "cluster-02-configure-playwright-runtime-sentinel"


class _StopAfterConfigure(RuntimeError):
    """Сентинел: до этой точки дошёл именно api_config.configure_playwright_runtime.

    Текст сообщения уникален - его ищем в залогированной ошибке, чтобы не
    спутать с исключением из predohranitel'nogo guard'а на реальный
    Playwright (см. _block_real_playwright_launch): до рефакторинга вызов
    идёт через локальную копию функции, она отрабатывает без ошибок, и код
    доходит до реального `sync_playwright()`, где его останавливает guard -
    но с ДРУГИМ текстом, так что маршрутизационный тест обязан упасть.
    """

    def __init__(self):
        super().__init__(_SENTINEL_TEXT)


@pytest.fixture(autouse=True)
def _block_real_playwright_launch(monkeypatch):
    """Не даёт тестам маршрутизации случайно запустить настоящий Playwright
    (сетевые вызовы/реальный браузер), если до рефакторинга место вызова
    ушло в свою локальную копию конфигурации, а не в подмену api_config."""

    def _guard(*args, **kwargs):
        raise AssertionError(
            "Реальный Playwright не должен запускаться в этом тесте: "
            "configure_playwright_runtime() не был перенаправлен на api_config"
        )

    monkeypatch.setattr(_real_playwright_sync_api, "sync_playwright", _guard)


def _patched_api_config_raises(monkeypatch):
    monkeypatch.setattr(
        qidian_workers.api_config,
        "configure_playwright_runtime",
        lambda: (_ for _ in ()).throw(_StopAfterConfigure()),
    )


def test_qidian_fetch_worker_run_routes_through_api_config(monkeypatch):
    _patched_api_config_raises(monkeypatch)
    monkeypatch.setattr(qidian_workers, "validate_source_url", lambda url: True)

    worker = qidian_workers.QidianFetchWorker("https://www.qidian.com/book/1041604040/")
    messages = []
    worker.log_signal.connect(lambda level, message: messages.append((level, message)))

    worker.run()

    error_messages = [message for level, message in messages if level == "ERROR"]
    assert any(_SENTINEL_TEXT in message for message in error_messages), (
        f"run() обязан звать api_config.configure_playwright_runtime(); залогировано: {error_messages}"
    )


def test_rulate_login_worker_run_routes_through_api_config(monkeypatch):
    _patched_api_config_raises(monkeypatch)

    worker = qidian_workers.RulateLoginWorker()
    messages = []
    worker.log_signal.connect(lambda level, message: messages.append((level, message)))

    worker.run()

    error_messages = [message for level, message in messages if level == "ERROR"]
    assert any(_SENTINEL_TEXT in message for message in error_messages), (
        f"run() обязан звать api_config.configure_playwright_runtime(); залогировано: {error_messages}"
    )


def test_rulate_fill_worker_run_routes_through_api_config(monkeypatch):
    _patched_api_config_raises(monkeypatch)

    worker = qidian_workers.RulateFillWorker(draft=None)
    messages = []
    worker.log_signal.connect(lambda level, message: messages.append((level, message)))

    worker.run()

    error_messages = [message for level, message in messages if level == "ERROR"]
    assert any(_SENTINEL_TEXT in message for message in error_messages), (
        f"run() обязан звать api_config.configure_playwright_runtime(); залогировано: {error_messages}"
    )


def test_fetch_qidian_cover_context_routes_through_api_config(monkeypatch):
    _patched_api_config_raises(monkeypatch)

    with pytest.raises(_StopAfterConfigure):
        qidian_workers._fetch_qidian_cover_context("https://www.qidian.com/book/1041604040/")


def test_fetch_fanqie_cover_context_routes_through_api_config(monkeypatch):
    _patched_api_config_raises(monkeypatch)
    monkeypatch.setattr(qidian_workers, "_fetch_fanqie_chapters_via_tomato", lambda *a, **k: [])

    with pytest.raises(_StopAfterConfigure):
        qidian_workers._fetch_fanqie_cover_context(
            "https://fanqienovel.com/page/7229603492648717324",
            original_description="",
        )


def test_fetch_ciweimao_cover_context_routes_through_api_config(monkeypatch):
    _patched_api_config_raises(monkeypatch)

    with pytest.raises(_StopAfterConfigure):
        qidian_workers._fetch_ciweimao_cover_context("https://www.ciweimao.com/book/100441110")
