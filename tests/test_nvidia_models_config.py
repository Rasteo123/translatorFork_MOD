import json
import unittest
from pathlib import Path


class NvidiaModelsConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config_path = Path(__file__).resolve().parents[1] / "config" / "api_providers.json"
        cls.providers = json.loads(config_path.read_text(encoding="utf-8"))

    def test_requested_nvidia_nim_models_are_configured_with_capabilities(self):
        models = self.providers["nvidia"]["models"]

        expected = {
            "DeepSeek V4 Flash NonThink (NVIDIA NIM)": {
                "id": "deepseek-ai/deepseek-v4-flash",
                "context_length": 1000000,
                "min_thinking_budget": False,
            },
            "DeepSeek V4 Flash Think (NVIDIA NIM)": {
                "id": "deepseek-ai/deepseek-v4-flash",
                "context_length": 1000000,
                "thinkingLevel": ["high", "max"],
            },
            "DeepSeek V4 Pro NonThink (NVIDIA NIM)": {
                "id": "deepseek-ai/deepseek-v4-pro",
                "context_length": 1000000,
                "min_thinking_budget": False,
            },
            "DeepSeek V4 Pro Think (NVIDIA NIM)": {
                "id": "deepseek-ai/deepseek-v4-pro",
                "context_length": 1000000,
                "thinkingLevel": ["high", "max"],
            },
            "Gemma 4 31B IT (NVIDIA NIM)": {
                "id": "google/gemma-4-31b-it",
                "context_length": 262144,
                "nvidia_reasoning": "gemma",
            },
            "Qwen3.5 122B A10B (NVIDIA NIM)": {
                "id": "qwen/qwen3.5-122b-a10b",
                "context_length": 262144,
                "nvidia_reasoning": "qwen",
            },
            "Qwen3.5 397B A17B (NVIDIA NIM)": {
                "id": "qwen/qwen3.5-397b-a17b",
                "context_length": 262144,
                "nvidia_reasoning": "qwen",
            },
        }

        for display_name, expected_fields in expected.items():
            with self.subTest(display_name=display_name):
                self.assertIn(display_name, models)
                model_config = models[display_name]
                for key, value in expected_fields.items():
                    self.assertEqual(model_config.get(key), value)
                self.assertIn("max_output_tokens", model_config)
                self.assertIn("needs_chunking", model_config)


if __name__ == "__main__":
    unittest.main()
