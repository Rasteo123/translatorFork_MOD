"""Регрессионные тесты для находок группы g08 (qidian_rulate/workers.py).

1) Незаэкранированный квантификатор {0,80} в f-строке SEO-паттернов
   _clean_qidian_description ломает очистку преамбулы, когда автор
   не найден (author="") или преамбула начинается не с имени автора.
2) `.first()` вызван как метод, а не как свойство Playwright Locator,
   в фолбэк-ветке _wait_for_selector_attached — фолбэк всегда падает.
"""


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


class _AttachedTimeout(Exception):
    pass


class _FakeFirstLocator:
    """Playwright-подобный Locator: `.first` — свойство, НЕ метод."""

    def __init__(self, page):
        self._page = page

    @property
    def first(self):
        return self

    def wait_for(self, *, state, timeout):
        self._page.fallback_calls.append((state, timeout))
        if self._page.element_appears_in_fallback:
            return None
        raise _AttachedTimeout("still absent")


class _FakePage:
    """Первичный wait_for_selector всегда «не успевает» (как в проде при
    позднем рендере), элемент появляется только в окне фолбэка."""

    def __init__(self, element_appears_in_fallback=True):
        self.element_appears_in_fallback = element_appears_in_fallback
        self.fallback_calls = []

    def wait_for_selector(self, selector, *, state, timeout):
        raise _AttachedTimeout("primary wait timed out")

    def locator(self, selector):
        return _FakeFirstLocator(self)


def test_wait_for_selector_attached_fallback_waits_for_late_element():
    """Фолбэк-ветка должна реально дождаться элемента через `.first`
    (свойство Playwright Locator), а не падать с TypeError на `.first()`.

    Раньше тест поднимал настоящий Chromium и гонял таймеры 200/300 мс — на
    медленном Windows-раннере окно фолбэка проигрывало гонку. Заглушки
    воспроизводят контракт Playwright (`.first` — свойство) детерминированно.
    """
    page = _FakePage(element_appears_in_fallback=True)

    # До фикса: фолбэк звал `.first()` -> TypeError ('Locator' object is not
    # callable), внешний except гасил его, и функция возвращала False.
    assert _wait_for_selector_attached(page, "#late-element", timeout=200) is True
    assert page.fallback_calls == [("attached", 200)]


def test_wait_for_selector_attached_returns_false_when_element_never_appears():
    page = _FakePage(element_appears_in_fallback=False)
    assert _wait_for_selector_attached(page, "#never", timeout=50) is False
    assert page.fallback_calls == [("attached", 50)]
