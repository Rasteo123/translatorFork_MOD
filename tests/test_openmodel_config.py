import json
import unittest
from pathlib import Path


class OpenModelConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config_path = Path(__file__).resolve().parents[1] / "config" / "api_providers.json"
        cls.providers = json.loads(config_path.read_text(encoding="utf-8"))

    def test_openmodel_deepseek_v4_flash_provider_is_configured(self):
        provider = self.providers["openmodel"]

        self.assertEqual(provider["handler_class"], "OpenModelApiHandler")
        self.assertEqual(provider["base_url"], "https://api.openmodel.ai")
        self.assertTrue(provider["visible"])

        model = provider["models"]["DeepSeek V4 Flash (OpenModel)"]
        self.assertEqual(model["id"], "deepseek-v4-flash")
        self.assertEqual(model["rpm"], 10)
        self.assertEqual(model["tpm"], 100000)
        self.assertEqual(model["max_concurrent_requests"], 10)
        self.assertEqual(model["context_length"], 1000000)
        self.assertEqual(model["max_output_tokens"], 8192)
        self.assertTrue(model["needs_chunking"])


if __name__ == "__main__":
    unittest.main()
