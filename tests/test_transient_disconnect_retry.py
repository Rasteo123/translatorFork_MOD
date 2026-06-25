import unittest

import aiohttp

from gemini_translator.api.base import BaseApiHandler
from gemini_translator.api.errors import NetworkError


class _SettingsManager:
    def __init__(self):
        self.increment_calls = 0
        self.decrement_calls = 0

    def increment_request_count(self, api_key, model_id):
        self.increment_calls += 1

    def decrement_request_count(self, api_key, model_id):
        self.decrement_calls += 1


class _WorkerStub:
    def __init__(self):
        self.api_key = "test-key"
        self.model_id = "test-model"
        self.model_config = {}
        self.provider_config = {
            "is_async": True,
            "base_timeout": 5,
            "transient_disconnect_retry_delay_seconds": 0,
        }
        self.settings_manager = _SettingsManager()
        self.is_cancelled = False
        self.is_shutting_down = False
        self.debug_logging_enabled = False


class _DisconnectOnceHandler(BaseApiHandler):
    def __init__(self, worker):
        super().__init__(worker)
        self.calls = 0

    async def call_api(self, *args, **kwargs):
        self.calls += 1
        if self.calls == 1:
            raise NetworkError("Network/SSL failure (ServerDisconnectedError): Server disconnected") from (
                aiohttp.ServerDisconnectedError()
            )
        return "ok"


class _AlwaysDisconnectHandler(_DisconnectOnceHandler):
    async def call_api(self, *args, **kwargs):
        self.calls += 1
        raise NetworkError("Network/SSL failure (ServerDisconnectedError): Server disconnected") from (
            aiohttp.ServerDisconnectedError()
        )


class TransientDisconnectRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_server_disconnected_is_retried_inside_api_call(self):
        worker = _WorkerStub()
        handler = _DisconnectOnceHandler(worker)

        result = await handler.execute_api_call("prompt", "[test]")

        self.assertEqual(result, "ok")
        self.assertEqual(handler.calls, 2)
        self.assertEqual(worker.settings_manager.increment_calls, 1)
        self.assertEqual(worker.settings_manager.decrement_calls, 0)

    async def test_server_disconnected_uses_normal_error_path_after_retry_budget(self):
        worker = _WorkerStub()
        handler = _AlwaysDisconnectHandler(worker)

        with self.assertRaises(NetworkError):
            await handler.execute_api_call("prompt", "[test]")

        self.assertEqual(handler.calls, 2)
        self.assertEqual(worker.settings_manager.increment_calls, 1)
        self.assertEqual(worker.settings_manager.decrement_calls, 1)


if __name__ == "__main__":
    unittest.main()
