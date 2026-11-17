import json
import unittest
from pathlib import Path


class ArbzModelsConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config_path = Path(__file__).resolve().parents[1] / "config" / "api_providers.json"
        cls.providers = json.loads(config_path.read_text(encoding="utf-8"))

    def test_arbz_models_are_configured(self):
        models = self.providers["arbz"]["models"]
        expected_ids = {
            "Gemini 4 Carbon High": "gemini-4-carbon-high",
            "Gemini 4 Carbon Medium": "gemini-4-carbon-medium",
            "Gemini 4 Carbon Low": "gemini-4-carbon-low",
            "Gemini 4 Argon High": "gemini-4-argon-high",
            "Gemini 4 Argon Medium": "gemini-4-argon-medium",
            "Gemini 4 Argon Low": "gemini-4-argon-low",
            "Gemini 4 Hydrogen XHigh": "gemini-4-hydrogen-xhigh",
            "Gemini 4 Hydrogen High": "gemini-4-hydrogen-high",
            "Gemini 4 Hydrogen Medium": "gemini-4-hydrogen-medium",
            "Gemini 4 Hydrogen Low": "gemini-4-hydrogen-low",
            "Gemini 3.8 Flash High": "gemini-3.8-flash-high",
            "Gemini 3.8 Flash Medium": "gemini-3.8-flash-medium",
            "Gemini 3.8 Flash Low": "gemini-3.8-flash-low",
            "Gemini 3.8 Flash (Arbz / Qroute)": "gemini-3.8-flash-tiered",
            "Claude Fable 5.1 Plus": "plus/claude-fable-5-1",
            "Claude Fable 5 Plus": "plus/claude-fable-5",
            "Claude Opus 5.5 Plus": "plus/claude-opus-5-5",
            "Claude Opus 5 Plus": "plus/claude-opus-5",
            "Claude Opus 4.8 Plus": "plus/claude-opus-4-8",
            "Claude Opus 4.7 Plus": "plus/claude-opus-4-7",
            "Claude Opus 4.6 Plus": "plus/claude-opus-4-6",
            "Claude Sonnet 5.5 Plus": "plus/claude-sonnet-5-5",
            "Claude Sonnet 5 Plus": "plus/claude-sonnet-5",
            "Claude Sonnet 4.6 Plus": "plus/claude-sonnet-4-6",
            "Claude Haiku 5.5 Plus": "plus/claude-haiku-5-5",
            "Claude Haiku 4.5 Plus": "plus/claude-haiku-4-5",
            "GPT-6.1 Sol Plus": "plus/gpt-6.1-sol",
            "GPT-6.1 Terra Plus": "plus/gpt-6.1-terra",
            "GPT-6 Astra Plus": "plus/gpt-6-astra",
            "GPT-6 Sol Plus": "plus/gpt-6-sol",
            "GPT-6 Luna Plus": "plus/gpt-6-luna",
            "GPT-5.6 Sol Plus": "plus/gpt-5.6-sol",
            "GPT-5.6 Terra Plus": "plus/gpt-5.6-terra",
            "GPT-5.6 Luna Plus": "plus/gpt-5.6-luna",
            "GPT-5.5 Plus": "plus/gpt-5.5",
            "Grok 4.7 Plus": "plus/grok-4.7",
            "Grok 4.6 Plus": "plus/grok-4.6",
            "Grok 4.5 Plus": "plus/grok-4.5",
            "Gemini 4 Argon Plus": "plus/gemini-4-argon",
            "Gemini 3.8 Flash Plus": "plus/gemini-3.8-flash",
            "Gemini 3.7 Flash Plus": "plus/gemini-3.7-flash",
            "DeepSeek V4 Pro Plus": "plus/deepseek-v4-pro",
            "DeepSeek V4.1 Flash 1017 Plus": "plus/deepseek-v4.1-flash-1017",
            "DeepSeek V4.1 Flash Plus": "plus/deepseek-v4.1-flash",
            "DeepSeek V4 Flash Plus": "plus/deepseek-v4-flash",
            "DeepSeek V4 Flash Vision Exp Plus": "plus/deepseek-v4-flash-vision-exp",
            "GLM 5.3 Plus": "plus/glm-5.3",
            "GLM 5.3 Flash Plus": "plus/glm-5.3-flash",
            "GLM 5.2 Plus": "plus/glm-5.2",
            "GLM 5.1 Plus": "plus/glm-5.1",
            "Kimi K3.1 Pro Plus": "plus/kimi-k3.1-pro",
            "Kimi K3 Plus": "plus/kimi-k3",
            "Kimi K2.7 Code Plus": "plus/kimi-k2.7-code",
            "Qwen 3.8 Max Plus": "plus/qwen3.8-max",
            "Qwen 3.8 Flash Plus": "plus/qwen3.8-flash",
            "Qwen 3.7 Max Plus": "plus/qwen3.7-max",
            "Qwen 3.7 Plus": "plus/qwen3.7-plus",
            "MiniMax M3.1 Flash Plus": "plus/minimax-m3.1-flash",
            "MiniMax M3 Plus": "plus/minimax-m3",
            "MiniMax M2.7 Plus": "plus/minimax-m2.7",
        }

        self.assertEqual(set(models), set(expected_ids))
        for display_name, model_id in expected_ids.items():
            with self.subTest(display_name=display_name):
                model = models[display_name]
                self.assertEqual(model["id"], model_id)
                self.assertEqual(model["rpm"], 6)
                self.assertTrue(model["needs_chunking"])
                self.assertEqual(model["max_concurrent_requests"], 3)
                self.assertEqual(model["max_output_tokens"], 8192)
                self.assertEqual(model["context_length"], 128000)

    def test_new_claude_models_use_native_messages_handler(self):
        provider = self.providers["arbz_claude"]
        self.assertEqual(provider["handler_class"], "OpenModelApiHandler")
        self.assertEqual(provider["base_url"], "https://ai.arbz.lol/v1/messages")
        self.assertTrue(provider["requires_api_key"])
        self.assertTrue(provider["is_async"])
        self.assertEqual(
            {model["id"] for model in provider["models"].values()},
            {"claude-opus-5", "claude-opus-4-8"},
        )


if __name__ == "__main__":
    unittest.main()
