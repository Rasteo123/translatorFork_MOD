import unittest
from types import SimpleNamespace

from gemini_translator.api.handlers.deepseek import DeepseekApiHandler


def _make_handler(model_config, **worker_attrs):
    worker = SimpleNamespace(
        provider_config={"is_async": True},
        model_config=model_config,
        **worker_attrs,
    )
    return DeepseekApiHandler(worker)


class DeepseekHandlerTests(unittest.TestCase):
    def test_model_without_thinking_config_leaves_payload_unchanged(self):
        handler = _make_handler({"id": "custom-deepseek-model"})
        payload = {"model": "custom-deepseek-model", "temperature": 0.7}

        handler._apply_deepseek_thinking_options(payload)

        self.assertEqual(payload, {"model": "custom-deepseek-model", "temperature": 0.7})

    def test_configured_non_thinking_disables_thinking(self):
        handler = _make_handler(
            {
                "id": "deepseek-v4-flash",
                "deepseek_thinking": "disabled",
                "min_thinking_budget": False,
            },
            thinking_enabled=True,
            thinking_level="MAX",
        )
        payload = {"model": "deepseek-v4-flash", "temperature": 0.7}

        handler._apply_deepseek_thinking_options(payload)

        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertNotIn("reasoning_effort", payload)
        self.assertEqual(payload["temperature"], 0.7)

    def test_configured_thinking_sets_effort_and_removes_temperature(self):
        handler = _make_handler(
            {
                "id": "deepseek-v4-pro",
                "deepseek_thinking": "enabled",
                "thinkingLevel": ["high", "max"],
                "min_thinking_budget": "high",
            },
            thinking_enabled=False,
            thinking_level="MAX",
        )
        payload = {"model": "deepseek-v4-pro", "temperature": 0.7}

        handler._apply_deepseek_thinking_options(payload)

        self.assertEqual(payload["thinking"], {"type": "enabled"})
        self.assertEqual(payload["reasoning_effort"], "max")
        self.assertNotIn("temperature", payload)


if __name__ == "__main__":
    unittest.main()
