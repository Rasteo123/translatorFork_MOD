import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gemini_translator.api import base as base_module
from gemini_translator.api.base import BaseApiHandler, get_worker_loop


class _WorkerStub:
    def __init__(self):
        self.provider_config = {"is_async": True, "base_timeout": 600}


class _FakeClientSession:
    def __init__(self, *args, **kwargs):
        self.closed = False
        self.close_calls = 0
        self.kwargs = kwargs

    async def close(self):
        self.closed = True
        self.close_calls += 1


class ProxySessionResetTests(unittest.TestCase):
    def setUp(self):
        self.handler = BaseApiHandler(_WorkerStub())

    def tearDown(self):
        loop = get_worker_loop()
        if not loop.is_closed():
            loop.run_until_complete(self.handler._close_thread_session_internal())
            loop.close()

        for attr in ("loop", "session", "session_proxy_signature", "session_timeout"):
            if hasattr(base_module._thread_local, attr):
                delattr(base_module._thread_local, attr)

    def test_proxy_change_recreates_cached_session(self):
        loop = get_worker_loop()

        with patch("gemini_translator.api.base.aiohttp.ClientSession", _FakeClientSession), \
             patch("gemini_translator.api.base.ProxyConnector") as proxy_connector:
            proxy_connector.from_url.side_effect = lambda url, rdns=True: {
                "url": url,
                "rdns": rdns,
            }

            self.handler.setup_client(
                proxy_settings={
                    "enabled": True,
                    "type": "SOCKS5",
                    "host": "proxy.example",
                    "port": 1080,
                    "user": "",
                    "pass": "",
                }
            )
            first_session = loop.run_until_complete(self.handler._get_or_create_session_internal(600))

            self.handler.setup_client(proxy_settings={"enabled": False})
            second_session = loop.run_until_complete(self.handler._get_or_create_session_internal(600))

        self.assertIsNot(first_session, second_session)
        self.assertTrue(first_session.closed)
        self.assertEqual(first_session.close_calls, 1)
        self.assertIsNone(second_session.kwargs["connector"])
        proxy_connector.from_url.assert_called_once_with("socks5://proxy.example:1080", rdns=True)


if __name__ == "__main__":
    unittest.main()
