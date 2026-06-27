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
            "ARBZ Translator": "translator",
            "Gemini 3.1 Pro High": "gemini-3.1-pro-high",
            "Gemini 3.1 Pro Low": "gemini-3.1-pro-low",
            "Gemini 3 Flash Agent": "gemini-3-flash-agent",
            "Gemini 3 Flash": "gemini-3-flash",
            "Gemini 3.5 Flash Low": "gemini-3.5-flash-low",
            "Gemini 3.5 Flash Extra Low": "gemini-3.5-flash-extra-low",
            "Gemini 3.1 Flash Lite": "gemini-3.1-flash-lite",
            "Gemini 2.5 Flash Lite": "gemini-2.5-flash-lite",
        }

        for display_name, model_id in expected_ids.items():
            with self.subTest(display_name=display_name):
                model = models[display_name]
                self.assertEqual(model["id"], model_id)
                self.assertEqual(model["rpm"], 6)
                self.assertTrue(model["needs_chunking"])
                self.assertEqual(model["max_concurrent_requests"], 3)
                self.assertEqual(model["max_output_tokens"], 8192)
                self.assertEqual(model["context_length"], 128000)


if __name__ == "__main__":
    unittest.main()
