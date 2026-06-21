import unittest
from types import SimpleNamespace

from gemini_translator.api.errors import PartialGenerationError
from gemini_translator.api.handlers.openmodel import OpenModelApiHandler


def _make_handler(model_config=None, provider_config=None, **worker_attrs):
    worker = SimpleNamespace(
        provider_config=provider_config or {"is_async": True, "base_url": "https://api.openmodel.ai"},
        model_config=model_config or {"id": "deepseek-v4-flash", "max_output_tokens": 8192},
        prompt_builder=SimpleNamespace(system_instruction=worker_attrs.pop("system_instruction", "")),
        temperature=worker_attrs.pop("temperature", 0.7),
        temperature_override_enabled=worker_attrs.pop("temperature_override_enabled", True),
        **worker_attrs,
    )
    return OpenModelApiHandler(worker)


class _ClientOverride:
    api_key = "om-test-key"


class OpenModelHandlerTests(unittest.TestCase):
    def test_setup_client_normalizes_root_endpoint_to_messages_api(self):
        handler = _make_handler()

        self.assertTrue(handler.setup_client(_ClientOverride()))

        self.assertEqual(handler.base_url, "https://api.openmodel.ai/v1/messages")
        self.assertEqual(handler.worker.model_id, "deepseek-v4-flash")
        self.assertEqual(handler.worker.api_key, "om-test-key")

    def test_build_payload_uses_anthropic_messages_shape(self):
        handler = _make_handler(system_instruction="Translate faithfully.")
        handler.worker.model_id = "deepseek-v4-flash"

        payload = handler._build_payload("Hello", use_stream=True)

        self.assertEqual(payload["model"], "deepseek-v4-flash")
        self.assertEqual(payload["system"], "Translate faithfully.")
        self.assertEqual(payload["messages"], [{"role": "user", "content": "Hello"}])
        self.assertEqual(payload["max_tokens"], 8192)
        self.assertTrue(payload["stream"])
        self.assertEqual(payload["temperature"], 0.7)

    def test_extract_non_streaming_text_from_messages_response(self):
        handler = _make_handler()
        result = {
            "content": [
                {"type": "text", "text": "Part 1"},
                {"type": "text", "text": " and part 2"},
            ],
            "stop_reason": "end_turn",
        }

        self.assertEqual(handler._extract_text_from_result(result), "Part 1 and part 2")

    def test_extract_non_streaming_max_tokens_raises_partial(self):
        handler = _make_handler()
        result = {"content": [{"type": "text", "text": "partial"}], "stop_reason": "max_tokens"}

        with self.assertRaises(PartialGenerationError) as ctx:
            handler._extract_text_from_result(result, allow_incomplete=False)

        self.assertEqual(ctx.exception.partial_text, "partial")
        self.assertEqual(ctx.exception.reason, "LENGTH")

    def test_extract_stream_text_delta_and_stop_reason(self):
        handler = _make_handler()

        text = handler._extract_stream_text_delta(
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "hello"}}
        )
        stop_reason = handler._extract_stop_reason(
            {"type": "message_delta", "delta": {"stop_reason": "max_tokens"}}
        )

        self.assertEqual(text, "hello")
        self.assertEqual(stop_reason, "max_tokens")


if __name__ == "__main__":
    unittest.main()
