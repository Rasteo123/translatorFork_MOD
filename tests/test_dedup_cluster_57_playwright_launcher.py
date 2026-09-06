"""cluster-57: Playwright Chromium launcher был скопирован из
ranobelib/workers.py в qidian_rulate/workers.py и разошёлся (headless-параметр
персистентного контекста, glob-паттерн поиска кэшированного Chromium,
мёртвая ветка в _is_browser_missing_error). Каноническая реализация теперь
живёт в qidian_rulate/playwright_launcher.py.

(a) Характеризационные тесты канонической реализации - крайние случаи,
    которые раньше различали копии.
(b) Тест-маршрутизация - оба site-модуля обязаны звать канонический
    launcher, а не свою копию.
"""

import os
import sys

import pytest

TESTS_DIR = os.path.dirname(__file__)
PROJECT_ROOT = os.path.dirname(TESTS_DIR)
RANOBELIB_DIR = os.path.join(PROJECT_ROOT, "ranobelib")

if RANOBELIB_DIR not in sys.path:
    sys.path.insert(0, RANOBELIB_DIR)

import workers as ranobelib_workers  # noqa: E402  (ranobelib/workers.py как top-level "workers")
from qidian_rulate import workers as qidian_workers  # noqa: E402
from qidian_rulate import playwright_launcher  # noqa: E402


# ---------------------------------------------------------------------------
# (a) Характеризационные тесты канонической реализации
# ---------------------------------------------------------------------------


def test_is_browser_missing_error_matches_mixed_case_browsertype_launch():
    """Копия в qidian_rulate сравнивала "browserType.launch" (смешанный
    регистр) со строкой, уже приведённой к .lower() - ветка была мёртвой.
    Каноническая версия (поведение ranobelib) обязана матчить такие
    сообщения, даже если они НЕ содержат "executable doesn't exist",
    "playwright install" или "chromium distribution ... not found".
    """
    error = RuntimeError("BrowserType.launch: unable to start executable, permission denied")

    assert playwright_launcher.is_browser_missing_error(error) is True


def test_is_browser_missing_error_false_for_unrelated_error():
    assert playwright_launcher.is_browser_missing_error(RuntimeError("network timeout")) is False


def test_find_cached_chromium_executable_extra_glob_matches_headless_shell(tmp_path, monkeypatch):
    """divergence: ranobelib ищет ещё и chrome-headless-shell, qidian - нет.
    Каноническая функция обязана искать headless-shell ТОЛЬКО когда вызывающий
    сайт явно передал extra_globs (сохраняет прежнее поведение обоих сайтов).
    """
    shell = (
        tmp_path
        / "chromium_headless_shell-1300"
        / "chrome-headless-shell-win64"
        / "chrome-headless-shell.exe"
    )
    shell.parent.mkdir(parents=True)
    shell.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        playwright_launcher, "_candidate_browser_cache_roots", lambda extra_roots=(): [tmp_path]
    )

    # Поведение qidian_rulate (без extra_globs) - headless-shell не находит.
    assert playwright_launcher._find_cached_chromium_executable() is None

    # Поведение ranobelib (с extra_globs) - находит.
    found = playwright_launcher._find_cached_chromium_executable(
        extra_globs=("chromium_headless_shell-*/chrome-headless-shell-win*/chrome-headless-shell.exe",)
    )
    assert found == shell


def test_candidate_browser_cache_roots_includes_extra_roots_only_when_passed(tmp_path):
    """divergence: qidian_rulate добавляет api_config-корни поиска,
    ranobelib - нет. Каноническая функция обязана добавлять их ТОЛЬКО когда
    вызывающий сайт передал extra_roots.
    """
    extra_root = tmp_path / "extra_dev_root"
    expected = extra_root / "playwright_runtime" / "ms-playwright"
    expected.mkdir(parents=True)

    roots_without_extra = playwright_launcher._candidate_browser_cache_roots()
    roots_with_extra = playwright_launcher._candidate_browser_cache_roots(extra_roots=(extra_root,))

    assert expected.resolve() not in roots_without_extra
    assert expected.resolve() in roots_with_extra


def test_launch_persistent_chromium_context_defaults_headless_false_and_viewport_optional():
    """Каноническая сигнатура - как у ranobelib (headless опционален,
    default False сохраняет старое жёстко закодированное поведение qidian)."""
    calls = []

    class FakeChromium:
        def launch_persistent_context(self, **kwargs):
            calls.append(kwargs)
            return "context"

    class FakePlaywright:
        chromium = FakeChromium()

    result = playwright_launcher.launch_persistent_chromium_context(
        FakePlaywright(), user_data_dir="/tmp/profile", args=["--x"],
    )

    assert result == "context"
    assert calls == [{"user_data_dir": "/tmp/profile", "headless": False, "args": ["--x"]}]


def test_launch_persistent_chromium_context_headless_true_is_passed_through():
    """Раньше qidian_rulate физически не мог открыть headless-персистентный
    контекст (headless=False зашит намертво). Канонический launcher обязан
    поддерживать headless=True (как ranobelib._has_saved_ranobelib_auth)."""
    calls = []

    class FakeChromium:
        def launch_persistent_context(self, **kwargs):
            calls.append(kwargs)
            return "context"

    class FakePlaywright:
        chromium = FakeChromium()

    playwright_launcher.launch_persistent_chromium_context(
        FakePlaywright(),
        user_data_dir="/tmp/profile",
        args=["--x"],
        headless=True,
        viewport={"width": 1, "height": 2},
    )

    assert calls == [
        {
            "user_data_dir": "/tmp/profile",
            "headless": True,
            "args": ["--x"],
            "viewport": {"width": 1, "height": 2},
        }
    ]


def test_launch_chromium_falls_back_through_cache_and_channels_then_raises_hint(monkeypatch):
    attempts = []

    class FakeChromium:
        def launch(self, **kwargs):
            attempts.append(kwargs)
            raise RuntimeError("Executable doesn't exist at /x/chrome\nplaywright install")

    class FakePlaywright:
        chromium = FakeChromium()

    monkeypatch.setattr(playwright_launcher, "_find_cached_chromium_executable", lambda **_: None)

    with pytest.raises(RuntimeError) as exc_info:
        playwright_launcher.launch_chromium(FakePlaywright(), headless=True, args=["--x"])

    assert "playwright install chromium" in str(exc_info.value)
    # обычный launch + 2 попытки через системные каналы (chrome, msedge) = 3
    assert len(attempts) == 3


# ---------------------------------------------------------------------------
# (b) Тест-маршрутизация: оба сайта обязаны звать канонический launcher
# ---------------------------------------------------------------------------


def test_qidian_launch_chromium_routes_through_canonical_launcher(monkeypatch):
    sentinel = object()
    captured = {}

    def fake_launch_chromium(playwright, **kwargs):
        captured["playwright"] = playwright
        captured["kwargs"] = kwargs
        return sentinel

    monkeypatch.setattr(playwright_launcher, "launch_chromium", fake_launch_chromium)

    fake_playwright = object()
    result = qidian_workers._launch_chromium(fake_playwright, headless=True, log_callback=None)

    assert result is sentinel
    assert captured["playwright"] is fake_playwright
    assert captured["kwargs"]["headless"] is True


def test_qidian_launch_persistent_context_routes_through_canonical_launcher_with_extra_roots(monkeypatch):
    sentinel = object()
    captured = {}

    def fake_launch_persistent(playwright, **kwargs):
        captured["kwargs"] = kwargs
        return sentinel

    monkeypatch.setattr(playwright_launcher, "launch_persistent_chromium_context", fake_launch_persistent)
    monkeypatch.setattr(qidian_workers.api_config, "get_executable_dir", lambda: "/exe/dir")
    monkeypatch.setattr(qidian_workers.api_config, "get_dev_project_root", lambda: "/dev/root")

    result = qidian_workers._launch_persistent_chromium_context(
        object(), user_data_dir="/tmp/x", viewport={"width": 1, "height": 2},
    )

    assert result is sentinel
    assert captured["kwargs"]["user_data_dir"] == "/tmp/x"
    extra_roots = [str(p) for p in captured["kwargs"]["extra_roots"]]
    assert "/exe/dir" in extra_roots
    assert "/dev/root" in extra_roots


def test_qidian_is_browser_missing_error_routes_through_canonical(monkeypatch):
    monkeypatch.setattr(playwright_launcher, "is_browser_missing_error", lambda error: "MARKER" in str(error))

    assert qidian_workers._is_browser_missing_error(RuntimeError("MARKER")) is True
    assert qidian_workers._is_browser_missing_error(RuntimeError("other")) is False


def test_ranobelib_launch_persistent_context_routes_through_canonical_launcher_with_extra_globs(monkeypatch):
    sentinel = object()
    captured = {}

    def fake_launch_persistent(playwright, **kwargs):
        captured["kwargs"] = kwargs
        return sentinel

    monkeypatch.setattr(playwright_launcher, "launch_persistent_chromium_context", fake_launch_persistent)

    result = ranobelib_workers._launch_persistent_chromium_context(
        object(), user_data_dir="/tmp/y", headless=True,
    )

    assert result is sentinel
    assert captured["kwargs"]["headless"] is True
    assert any("headless_shell" in g for g in captured["kwargs"]["extra_globs"])


def test_ranobelib_is_browser_missing_error_routes_through_canonical(monkeypatch):
    monkeypatch.setattr(playwright_launcher, "is_browser_missing_error", lambda error: "MARKER" in str(error))

    assert ranobelib_workers._is_browser_missing_error(RuntimeError("MARKER")) is True
    assert ranobelib_workers._is_browser_missing_error(RuntimeError("other")) is False


def test_ranobelib_find_cached_chromium_executable_routes_through_canonical_with_extra_globs(monkeypatch):
    captured = {}

    def fake_find(**kwargs):
        captured["kwargs"] = kwargs
        return None

    monkeypatch.setattr(playwright_launcher, "_find_cached_chromium_executable", fake_find)

    ranobelib_workers._find_cached_chromium_executable()

    assert any("headless_shell" in g for g in captured["kwargs"]["extra_globs"])
