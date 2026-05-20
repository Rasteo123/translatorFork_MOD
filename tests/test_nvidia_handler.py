import unittest
from types import SimpleNamespace

from gemini_translator.api.handlers.nvidia import NvidiaApiHandler


def _make_handler(model_config, **worker_attrs):
    worker = SimpleNamespace(
        provider_config={"is_async": True},
        model_config=model_config,
        temperature=worker_attrs.pop("temperature", 0.7),
        temperature_override_enabled=worker_attrs.pop("temperature_override_enabled", False),
        thinking_enabled=worker_attrs.pop("thinking_enabled", False),
        thinking_level=worker_attrs.pop("thinking_level", None),
        **worker_attrs,
    )
    return NvidiaApiHandler(worker)


class NvidiaHandlerModelOptionsTests(unittest.TestCase):
    def test_deepseek_thinking_sets_effort_and_removes_sampling_temperature(self):
        handler = _make_handler(
            {
                "id": "deepseek-ai/deepseek-v4-pro",
                "nvidia_reasoning": "deepseek",
                "deepseek_thinking": "enabled",
                "thinkingLevel": ["high", "max"],
                "default_reasoning_effort": "high",
            },
            thinking_level="MAX",
        )
        payload = {"model": "deepseek-ai/deepseek-v4-pro", "temperature": 0.7}

        self.assertTrue(hasattr(handler, "_apply_nvidia_model_options"))
        handler._apply_nvidia_model_options(payload)

        self.assertEqual(payload["thinking"], {"type": "enabled"})
        self.assertEqual(payload["reasoning_effort"], "max")
        self.assertNotIn("temperature", payload)

    def test_qwen_thinking_uses_chat_template_kwargs_and_thinking_sampling_defaults(self):
        handler = _make_handler(
            {
                "id": "qwen/qwen3.5-397b-a17b",
                "nvidia_reasoning": "qwen",
                "default_temperature": 0.7,
                "default_thinking_temperature": 0.6,
                "top_p": 0.8,
                "thinking_top_p": 0.95,
                "top_k": 20,
                "min_p": 0,
                "min_thinking_budget": -1,
            },
            thinking_enabled=True,
            temperature=0.7,
            temperature_override_enabled=False,
        )
        payload = {"model": "qwen/qwen3.5-397b-a17b", "temperature": 0.7}

        self.assertTrue(hasattr(handler, "_apply_nvidia_model_options"))
        handler._apply_nvidia_model_options(payload)

        self.assertEqual(payload["chat_template_kwargs"], {"enable_thinking": True})
        self.assertEqual(payload["temperature"], 0.6)
        self.assertEqual(payload["top_p"], 0.95)
        self.assertEqual(payload["top_k"], 20)
        self.assertEqual(payload["min_p"], 0)

    def test_gemma_thinking_injects_system_think_token(self):
        handler = _make_handler(
            {
                "id": "google/gemma-4-31b-it",
                "nvidia_reasoning": "gemma",
                "min_thinking_budget": -1,
            },
            thinking_enabled=True,
        )
        messages = [
            {"role": "system", "content": "Translate faithfully."},
            {"role": "user", "content": "Hello"},
        ]
        payload = {"model": "google/gemma-4-31b-it", "messages": messages}

        self.assertTrue(hasattr(handler, "_apply_nvidia_model_options"))
        handler._apply_nvidia_model_options(payload)

        self.assertEqual(payload["messages"][0]["role"], "system")
        self.assertEqual(payload["messages"][0]["content"], "<|think|>\nTranslate faithfully.")

    def test_clean_response_text_strips_think_and_gemma_thought_blocks(self):
        handler = _make_handler({"strip_reasoning_tags": True})

        self.assertEqual(handler._clean_response_text("<think>hidden</think>\nfinal"), "final")
        self.assertEqual(
            handler._clean_response_text("<|channel>thought\nhidden reasoning<channel|>final"),
            "final",
        )


if __name__ == "__main__":
    unittest.main()
