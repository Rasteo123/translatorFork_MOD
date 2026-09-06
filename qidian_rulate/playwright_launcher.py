# -*- coding: utf-8 -*-
"""Общий Playwright Chromium launcher с fallback на кэш/системный браузер.

Каноническая реализация для cluster-57 (устранение дубля): раньше этот
лаунчер был скопирован из ranobelib/workers.py в qidian_rulate/workers.py и
разошёлся по возможностям (headless-параметр персистентного контекста,
glob-паттерн поиска кэшированного Chromium, набор корней поиска). Здесь -
единая логика запуска/ретраев/fallback; каждый сайт передаёт свои
site-specific `extra_roots`/`extra_globs`, не дублируя саму логику запуска.

Модуль не зависит от PyQt/Playwright на уровне импортов - Playwright
передаётся вызывающим кодом как объект `playwright` (результат
`sync_playwright()`), а `args` (BROWSER_ARGS) передаётся вызывающим кодом,
чтобы этот модуль оставался независимым от конкретного сайта.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path


def _playwright_browser_install_hint() -> str:
    python_executable = sys.executable or "python"
    return (
        "Playwright не нашел совместимый Chromium. "
        f"Установите браузер командой: \"{python_executable}\" -m playwright install chromium"
    )


def is_browser_missing_error(error: Exception) -> bool:
    """Определяет, что Playwright не смог найти/запустить Chromium.

    Каноническая версия матчинга (см. cluster-57 divergence): литерал
    сравнивается в нижнем регистре ("browsertype.launch"), т.к. `text` уже
    приведён к нижнему регистру через `.lower()` - копия в qidian_rulate
    содержала литерал "browserType.launch" со смешанным регистром, из-за
    чего эта ветка никогда не срабатывала (мёртвый код). Здесь взята
    рабочая версия из ranobelib, что расширяет матчинг ошибок и для
    qidian_rulate/workers.py.
    """
    text = str(error).lower()
    return (
        "executable doesn't exist" in text
        or "playwright install" in text
        or ("browsertype.launch" in text and "executable" in text)
        or ("chromium distribution" in text and "not found" in text)
    )


def _candidate_browser_cache_roots(extra_roots=()) -> list[Path]:
    roots: list[Path] = []
    env_value = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if env_value:
        roots.append(Path(env_value))

    module_root = Path(__file__).resolve().parents[1]
    bases = [module_root, *extra_roots, Path.cwd()]
    for base in bases:
        if base:
            roots.append(Path(base) / "playwright_runtime" / "ms-playwright")

    localappdata = os.environ.get("LOCALAPPDATA")
    if localappdata:
        roots.append(Path(localappdata) / "ms-playwright")

    unique = []
    seen = set()
    for root in roots:
        try:
            resolved = root.resolve()
        except Exception:
            resolved = root
        key = str(resolved).lower()
        if key not in seen and resolved.exists() and resolved.is_dir():
            seen.add(key)
            unique.append(resolved)
    return unique


def _revision_from_path(path: Path) -> int:
    match = re.search(r"chromium-(\d+)", str(path))
    if not match:
        return -1
    return int(match.group(1))


_BASE_CHROMIUM_GLOB = "chromium-*/chrome-win*/chrome.exe"


def _find_cached_chromium_executable(*, extra_roots=(), extra_globs=()) -> Path | None:
    candidates: list[Path] = []
    globs = (_BASE_CHROMIUM_GLOB, *extra_globs)
    for root in _candidate_browser_cache_roots(extra_roots=extra_roots):
        for pattern in globs:
            candidates.extend(root.glob(pattern))
    existing = [candidate for candidate in candidates if candidate.exists() and candidate.is_file()]
    if not existing:
        return None
    return max(existing, key=_revision_from_path)


def launch_chromium(
    playwright,
    *,
    headless: bool,
    args,
    extra_roots=(),
    extra_globs=(),
    log_callback=None,
):
    """Запускает обычный (не персистентный) Chromium с fallback-цепочкой."""
    try:
        return playwright.chromium.launch(headless=headless, args=args)
    except Exception as error:
        if not is_browser_missing_error(error):
            raise
        if log_callback:
            log_callback("WARNING", "Playwright Chromium не найден, пробую fallback-браузер.")

    cached_executable = _find_cached_chromium_executable(extra_roots=extra_roots, extra_globs=extra_globs)
    if cached_executable:
        try:
            if log_callback:
                log_callback("INFO", f"Playwright: запускаю Chromium из {cached_executable}.")
            return playwright.chromium.launch(
                executable_path=str(cached_executable),
                headless=headless,
                args=args,
            )
        except Exception as error:
            if log_callback:
                log_callback("WARNING", f"Кэшированный Chromium не запустился: {error}")

    for channel in ("chrome", "msedge"):
        try:
            if log_callback:
                log_callback("INFO", f"Playwright: пробую системный браузер {channel}.")
            return playwright.chromium.launch(channel=channel, headless=headless, args=args)
        except Exception as error:
            if log_callback:
                log_callback("WARNING", f"Системный браузер {channel} не запустился: {error}")

    raise RuntimeError(_playwright_browser_install_hint())


def launch_persistent_chromium_context(
    playwright,
    *,
    user_data_dir: str,
    args,
    viewport: dict | None = None,
    headless: bool = False,
    extra_roots=(),
    extra_globs=(),
    log_callback=None,
):
    """Запускает персистентный Chromium-контекст с fallback-цепочкой."""
    kwargs = {
        "user_data_dir": user_data_dir,
        "headless": headless,
        "args": args,
    }
    if viewport:
        kwargs["viewport"] = viewport

    try:
        return playwright.chromium.launch_persistent_context(**kwargs)
    except Exception as error:
        if not is_browser_missing_error(error):
            raise
        if log_callback:
            log_callback("WARNING", "Playwright Chromium не найден, пробую fallback-браузер.")

    cached_executable = _find_cached_chromium_executable(extra_roots=extra_roots, extra_globs=extra_globs)
    if cached_executable:
        try:
            if log_callback:
                log_callback("INFO", f"Playwright: запускаю Chromium из {cached_executable}.")
            return playwright.chromium.launch_persistent_context(
                **kwargs,
                executable_path=str(cached_executable),
            )
        except Exception as error:
            if log_callback:
                log_callback("WARNING", f"Кэшированный Chromium не запустился: {error}")

    for channel in ("chrome", "msedge"):
        try:
            if log_callback:
                log_callback("INFO", f"Playwright: пробую системный браузер {channel}.")
            return playwright.chromium.launch_persistent_context(**kwargs, channel=channel)
        except Exception as error:
            if log_callback:
                log_callback("WARNING", f"Системный браузер {channel} не запустился: {error}")

    raise RuntimeError(_playwright_browser_install_hint())
