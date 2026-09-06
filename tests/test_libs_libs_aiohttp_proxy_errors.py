"""
Библиотечный вердикт libs-aiohttp-proxy (aiohttp_socks / PySocks), пункт (1).

aiohttp_socks оборачивает исключения python_socks в СВОИ собственные классы
(aiohttp_socks._errors.ProxyError/ProxyConnectionError/ProxyTimeoutError —
прямые наследники Exception, не OSError). base.PROXY_ERRORS до фикса содержал
только socks.* (python_socks), поэтому отказ SOCKS-прокси на async-пути
(aiohttp_socks.ProxyConnector) не попадал под классификацию NetworkError в
_process_exception_and_counters и улетал наружу голым `raise e`.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

import aiohttp_socks

from gemini_translator.api.base import BaseApiHandler
from gemini_translator.api.errors import NetworkError


def _make_handler():
    worker = SimpleNamespace(
        provider_config={"is_async": True, "base_timeout": 600},
        settings_manager=MagicMock(),
        api_key="test-key-ABCD",
        model_id="test-model",
    )
    return BaseApiHandler(worker)


class AiohttpSocksProxyErrorsClassifiedAsNetworkErrorTests(unittest.TestCase):
    def test_proxy_connection_error_becomes_network_error(self):
        handler = _make_handler()
        original = aiohttp_socks.ProxyConnectionError("SOCKS proxy отказал")

        with self.assertRaises(NetworkError) as ctx:
            handler._process_exception_and_counters(original)

        self.assertEqual(ctx.exception.delay_seconds, 30)
        self.assertIs(ctx.exception.__cause__, original)

    def test_proxy_timeout_error_becomes_network_error(self):
        handler = _make_handler()
        original = aiohttp_socks.ProxyTimeoutError("SOCKS proxy timeout")

        with self.assertRaises(NetworkError) as ctx:
            handler._process_exception_and_counters(original)

        self.assertEqual(ctx.exception.delay_seconds, 30)

    def test_generic_proxy_error_becomes_network_error(self):
        handler = _make_handler()
        original = aiohttp_socks.ProxyError("SOCKS handshake failed")

        with self.assertRaises(NetworkError) as ctx:
            handler._process_exception_and_counters(original)

        self.assertEqual(ctx.exception.delay_seconds, 30)


if __name__ == "__main__":
    unittest.main()


class AiohttpSocksProxyErrorsResetTheSessionTests(unittest.TestCase):
    """PROXY_ERRORS должны участвовать и в логике сброса сессии, а не только в
    классификации: у общего ProxyError текст («Unexpected SOCKS version number»)
    не содержит «connection», так что текстовая эвристика его не ловит, а
    коннектор после такого сбоя может остаться в битом состоянии."""

    def test_generic_proxy_error_triggers_session_reset(self):
        handler = _make_handler()
        resets = []
        handler._force_session_reset = lambda: resets.append(True)
        original = aiohttp_socks.ProxyError("Unexpected SOCKS version number")

        with self.assertRaises(NetworkError):
            handler._process_exception_and_counters(original)

        self.assertEqual(resets, [True], "после ошибки SOCKS-прокси сессия обязана быть сброшена")
