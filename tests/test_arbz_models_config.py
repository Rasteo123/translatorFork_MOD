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
            "Gemini 4 Argon High": "gemini-4-argon-high",
            "Gemini 4 Argon Medium": "gemini-4-argon-medium",
            "Gemini 4 Argon Low": "gemini-4-argon-low",
            "Gemini 3.8 Flash High": "gemini-3.8-flash-high",
            "Gemini 3.8 Flash Medium": "gemini-3.8-flash-medium",
            "Gemini 3.8 Flash Low": "gemini-3.8-flash-low",
            "Gemini 3.7 Flash Tiered": "gemini-3.7-flash-tiered",
            "Gemini 3.6 Flash High": "gemini-3.6-flash-high",
            "Gemini 3.6 Flash Low": "gemini-3.6-flash-low",
            "Gemini 3.5 Flash High": "gemini-3-flash-agent",
            "Gemini 3.5 Flash Low": "gemini-3.5-flash-low",
            "Claude Opus 4.6": "claude-opus-4-6",
            "Claude Sonnet 4.6": "claude-sonnet-4-6",
            "GPT-5.5": "gpt-5.5",
            "GPT-6 Astra Plus": "plus/gpt-6-astra",
            "GPT-6.1 Sol Plus": "plus/gpt-6.1-sol",
            "GPT-6 Sol Plus": "plus/gpt-6-sol",
            "GPT-6 Luna Plus": "plus/gpt-6-luna",
            "GPT-5.6 Sol Plus": "plus/gpt-5.6-sol",
            "GPT-5.6 Terra Plus": "plus/gpt-5.6-terra",
            "GPT-5.6 Luna Plus": "plus/gpt-5.6-luna",
            "GPT-5.5 Plus": "plus/gpt-5.5",
            "Grok 4.7 Plus": "plus/grok-4.7",
            "Grok 4.6 Plus": "plus/grok-4.6",
            "Grok 4.5 Plus": "plus/grok-4.5",
            "Grok 4.3 Plus": "plus/grok-4.3",
            "Grok Code Fast 1 Plus": "plus/grok-code-fast-1",
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
