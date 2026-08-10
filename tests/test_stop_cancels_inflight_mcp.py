import asyncio
import unittest
from unittest import mock

from main import EventBus

from gemini_translator.api.handlers.mcp import McpApiHandler
from gemini_translator.mcp import inflight


class _DummyPromptBuilder:
    system_instruction = ""


class _DummyWorker:
    def __init__(self):
        self.api_key = "k"
        self.model_id = "mcp-client"
        self.model_config = {"id": "mcp-client"}
        self.provider_config = {"base_timeout": 30}
        self.prompt_builder = _DummyPromptBuilder()
        self.sync_executor = None

    def _post_event(self, *args, **kwargs):
        pass


def _make_handler():
    handler = McpApiHandler.__new__(McpApiHandler)
    handler.worker = _DummyWorker()
    return handler


class EmitEventCancelsInflightTests(unittest.TestCase):
    def setUp(self):
        inflight.clear()
        self.addCleanup(inflight.clear)
        self.bus = EventBus()
        self.addCleanup(self.bus.deleteLater)

    def test_manual_stop_cancels_inflight_mcp_requests(self):
        with mock.patch.object(inflight, "cancel_all") as cancel_all:
            self.bus.emit_event({"event": "manual_stop_requested", "source": "CorrectionDialog"})

        cancel_all.assert_called_once()

    def test_stop_session_also_cancels(self):
        with mock.patch.object(inflight, "cancel_all") as cancel_all:
            self.bus.emit_event({"event": "stop_session_requested", "source": "TranslationEngine"})

        cancel_all.assert_called_once()

    def test_ordinary_events_do_not_cancel_anything(self):
        with mock.patch.object(inflight, "cancel_all") as cancel_all:
            self.bus.emit_event({"event": "tasks_added", "source": "Engine", "data": {"count": 1}})

        cancel_all.assert_not_called()

    def test_a_broken_cancel_never_blocks_the_stop_event(self):
        seen = []
        self.bus.event_posted.connect(lambda event: seen.append(event.get("event")))

        with mock.patch.object(inflight, "cancel_all", side_effect=RuntimeError("boom")):
            self.bus.emit_event({"event": "manual_stop_requested", "source": "CorrectionDialog"})

        self.assertEqual(seen, ["manual_stop_requested"])


class HandlerRegistersInflightTests(unittest.TestCase):
    def setUp(self):
        inflight.clear()
        self.addCleanup(inflight.clear)

    def _run(self, handler, request_completion):
        with mock.patch.object(McpApiHandler, "_request_completion", request_completion):
            return asyncio.run(handler.execute_api_call("prompt", "log"))

    def test_request_id_is_registered_while_the_call_is_running(self):
        handler = _make_handler()
        seen = {}

        def request_completion(_self, payload):
            seen["inflight"] = inflight.snapshot()
            seen["request_id"] = payload["request_id"]
            return {"ok": True, "text": "готово"}

        self._run(handler, request_completion)

        self.assertEqual(seen["inflight"], [seen["request_id"]])

    def test_request_id_is_released_after_success(self):
        handler = _make_handler()
        self._run(handler, lambda _self, payload: {"ok": True, "text": "готово"})

        self.assertEqual(inflight.snapshot(), [])

    def test_request_id_is_released_after_failure(self):
        handler = _make_handler()

        def boom(_self, payload):
            raise RuntimeError("network down")

        with self.assertRaises(Exception):
            self._run(handler, boom)

        self.assertEqual(inflight.snapshot(), [])


if __name__ == "__main__":
    unittest.main()
