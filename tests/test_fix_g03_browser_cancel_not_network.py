# Дефект: BrowserApiHandler.call_api оборачивает OperationCancelledError
# (отмена пользователем по кнопке "Стоп") в NetworkError через общий
# except Exception, попутно пытаясь пересоздать страницу браузера.
# Находки: api/bugs/4-browser-cancel-as-network-erro, api/design/1-browser-cancel-wrapped-as-netw.
#
# Тест гоняет боевое тело BrowserApiHandler.call_api на минимальном
# харнессе (без реального Playwright/сети): фейковые page/context/locator
# плюс воркер с is_cancelled=True.

import asyncio
import types
import unittest

from gemini_translator.api.errors import NetworkError, OperationCancelledError
from gemini_translator.api.handlers.browser import BrowserApiHandler


class FakeLocator:
    """Минимальная имитация playwright Locator, достаточная для call_api."""

    def __init__(self, text="ответ модели"):
        self._text = text

    @property
    def last(self):
        return self

    async def is_visible(self):
        return True

    async def is_enabled(self):
        return True

    async def fill(self, value):
        return None

    async def click(self):
        return None

    async def press(self, key):
        return None

    async def wait_for(self, state="visible", timeout=None):
        return None

    async def inner_text(self):
        return self._text


class FakePage:
    def __init__(self):
        self.url = "https://chat.example/"
        self.closed = False
        self.close_calls = 0

    def is_closed(self):
        return self.closed

    def locator(self, selector):
        return FakeLocator()

    async def wait_for_selector(self, selector, state="visible", timeout=None):
        return None

    async def close(self):
        self.close_calls += 1
        self.closed = True


class FakeContext:
    def __init__(self):
        self.new_page_calls = 0

    async def new_page(self):
        self.new_page_calls += 1
        return FakePage()


def _make_handler(is_cancelled):
    """Собирает BrowserApiHandler в обход __init__ (не трогаем реальный Playwright)."""
    handler = BrowserApiHandler.__new__(BrowserApiHandler)
    handler.worker = types.SimpleNamespace(
        is_cancelled=is_cancelled,
        provider_config={},
        debug_logging_enabled=False,
    )
    handler.service_url = "https://chat.example/"
    handler.selectors = {
        "input_box": "#input",
        "send_button": "#send",
        "last_message": ".markdown",
    }
    handler.is_logged_in = True
    handler.page = FakePage()
    handler.context = FakeContext()
    handler.proxy_settings = None
    return handler


class BrowserCancelNotNetworkErrorTests(unittest.TestCase):
    def test_user_cancellation_propagates_as_operation_cancelled_not_network(self):
        handler = _make_handler(is_cancelled=True)

        with self.assertRaises(OperationCancelledError) as ctx:
            asyncio.run(handler.call_api("prompt", "[TEST]"))

        self.assertNotIsInstance(ctx.exception, NetworkError)

    def test_cancellation_does_not_recreate_the_browser_page(self):
        # При штатной отмене не нужно закрывать/пересоздавать страницу —
        # сессия браузера должна остаться пригодной для следующего запроса.
        handler = _make_handler(is_cancelled=True)
        original_page = handler.page

        with self.assertRaises(OperationCancelledError):
            asyncio.run(handler.call_api("prompt", "[TEST]"))

        self.assertEqual(original_page.close_calls, 0)
        self.assertIs(handler.page, original_page)
        self.assertEqual(handler.context.new_page_calls, 0)


if __name__ == "__main__":
    unittest.main()
