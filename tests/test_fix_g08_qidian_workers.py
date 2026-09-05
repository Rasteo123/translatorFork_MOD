"""Регрессионные тесты для находок группы g08 (qidian_rulate/workers.py).

1) Незаэкранированный квантификатор {0,80} в f-строке SEO-паттернов
   _clean_qidian_description ломает очистку преамбулы, когда автор
   не найден (author="") или преамбула начинается не с имени автора.
2) `.first()` вызван как метод, а не как свойство Playwright Locator,
   в фолбэк-ветке _wait_for_selector_attached — фолбэк всегда падает.
"""

import pytest

from qidian_rulate.workers import _clean_qidian_description, _wait_for_selector_attached


def test_clean_qidian_description_strips_seo_prefix_without_author():
    """При author="" (автор не найден) дефолтные SEO-паттерны всё равно
    должны вырезать преамбулу вида "XXX创作的《Title》，已更新...最新章节：...。".
    """
    raw_description = (
        "盲候创作的奇幻小说《冒牌领主》，已更新227章，"
        "最新章节：第226章 瑟银要塞陷落。"
        "罗南穿越而来，成了贵族大少的背锅替身。"
    )

    cleaned = _clean_qidian_description(raw_description, title="冒牌领主", author="")

    assert cleaned.startswith("罗南穿越而来"), cleaned
    assert "最新章节" not in cleaned
    assert "创作的" not in cleaned


def test_wait_for_selector_attached_fallback_waits_for_late_element():
    """Фолбэк-ветка должна реально дождаться элемента через `.first`
    (свойство Playwright Locator), а не падать с TypeError на `.first()`.
    """
    playwright_sync_api = pytest.importorskip("playwright.sync_api")

    with playwright_sync_api.sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content("<html><body></body></html>")
            # Элемент появляется в DOM через 300 мс — после того, как первичный
            # page.wait_for_selector с таймаутом 200мс не успеет, но в пределах
            # окна фолбэка (ещё 200мс, итого до 400мс).
            page.evaluate(
                """
                setTimeout(() => {
                    const el = document.createElement('div');
                    el.id = 'late-element';
                    document.body.appendChild(el);
                }, 300);
                """
            )

            result = _wait_for_selector_attached(page, "#late-element", timeout=200)

            # До фикса: первичный wait_for_selector(timeout=200) не успевает,
            # фолбэк `.first()` бросает TypeError ('Locator' object is not
            # callable), внешний except гасит его, и функция сразу
            # возвращает False, не дождавшись элемента.
            assert result is True
        finally:
            browser.close()
