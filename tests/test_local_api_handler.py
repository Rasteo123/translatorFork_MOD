import unittest
from types import SimpleNamespace
from unittest.mock import patch

from gemini_translator.api.errors import PartialGenerationError
from gemini_translator.api.handlers.local import LocalApiHandler


class _DummyResponse:
    status_code = 200
    text = ""

    def __init__(self, finish_reason="stop", content="ok"):
        self._payload = {
            "choices": [
                {
                    "finish_reason": finish_reason,
                    "message": {"content": content},
                }
            ]
        }

    def json(self):
        return self._payload


class _WorkerStub:
    def __init__(self, model_config=None):
        self.provider_config = {
            "base_url": "http://127.0.0.1:11434/v1/chat/completions",
            "is_async": False,
        }
        self.model_config = {
            "id": "local-model",
            "base_url": "http://127.0.0.1:11434/v1/chat/completions",
        }
        if model_config:
            self.model_config.update(model_config)
        self.prompt_builder = SimpleNamespace(system_instruction=None)
        self.temperature = 0.2
        self.temperature_override_enabled = True
        self.api_key = ""
        self.model_id = ""
        self.events = []

    def _post_event(self, event, payload):
        self.events.append((event, payload))


class LocalApiHandlerTests(unittest.TestCase):
    def _make_handler(self, model_config=None):
        worker = _WorkerStub(model_config=model_config)
        handler = LocalApiHandler(worker)
        handler.setup_client(SimpleNamespace(api_key="local-key"))
        return handler, worker

    def test_allow_incomplete_does_not_send_default_max_tokens(self):
        handler, _worker = self._make_handler()
        captured_payloads = []

        def fake_post(url, headers=None, json=None, proxies=None, timeout=None):
            captured_payloads.append(json)
            return _DummyResponse()

        with patch("gemini_translator.api.handlers.local.requests.post", side_effect=fake_post):
            result = handler.call_api("prompt", "log", allow_incomplete=True)

        self.assertEqual(result, "ok")
        self.assertNotIn("max_tokens", captured_payloads[0])

    def test_allow_incomplete_uses_explicit_model_output_limit(self):
        handler, _worker = self._make_handler({"max_output_tokens": 10000})
        captured_payloads = []

        def fake_post(url, headers=None, json=None, proxies=None, timeout=None):
            captured_payloads.append(json)
            return _DummyResponse()

        with patch("gemini_translator.api.handlers.local.requests.post", side_effect=fake_post):
            result = handler.call_api("prompt", "log", allow_incomplete=True)

        self.assertEqual(result, "ok")
        self.assertEqual(captured_payloads[0]["max_tokens"], 9800)

    def test_temperature_is_omitted_when_override_is_disabled(self):
        handler, worker = self._make_handler()
        worker.temperature_override_enabled = False
        captured_payloads = []

        def fake_post(url, headers=None, json=None, proxies=None, timeout=None):
            captured_payloads.append(json)
            return _DummyResponse()

        with patch("gemini_translator.api.handlers.local.requests.post", side_effect=fake_post):
            result = handler.call_api("prompt", "log", allow_incomplete=True)

        self.assertEqual(result, "ok")
        self.assertNotIn("temperature", captured_payloads[0])

    def test_length_finish_reason_raises_partial_with_limit_source(self):
        handler, worker = self._make_handler()

        def fake_post(url, headers=None, json=None, proxies=None, timeout=None):
            return _DummyResponse(finish_reason="length", content='{"broken":')

        with patch("gemini_translator.api.handlers.local.requests.post", side_effect=fake_post):
            with self.assertRaises(PartialGenerationError) as caught:
                handler.call_api("prompt", "log", allow_incomplete=True)

        self.assertEqual(caught.exception.partial_text, '{"broken":')
        self.assertTrue(
            any(
                "client max_tokens was not set" in payload.get("message", "")
                for event, payload in worker.events
                if event == "log_message"
            )
        )


if __name__ == "__main__":
    unittest.main()
