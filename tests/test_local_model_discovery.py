import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from gemini_translator.api import config as api_config


class _DummyResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class LocalModelDiscoveryTests(unittest.TestCase):
    def setUp(self):
        api_config.initialize_configs()

    def tearDown(self):
        api_config.initialize_configs()

    def test_refresh_dynamic_models_uses_server_inventory_for_local_provider(self):
        def fake_get(url, timeout=None):
            if url == "http://127.0.0.1:11434/api/tags":
                return _DummyResponse(
                    200,
                    {
                        "models": [
                            {"name": "deepseek-r1:8b"},
                            {"name": "llama3.1:8b"},
                        ]
                    },
                )
            if url == "http://127.0.0.1:11434/v1/models":
                return _DummyResponse(404, {})
            if url == "http://localhost:1234/api/tags":
                return _DummyResponse(404, {})
            if url == "http://localhost:1234/v1/models":
                return _DummyResponse(
                    200,
                    {
                        "data": [
                            {"id": "google/gemma-3-12b", "max_context_length": 262144},
                        ]
                    },
                )
            raise AssertionError(f"Unexpected discovery URL: {url}")

        with patch.dict(os.environ, {"GT_DISABLE_LOCAL_MODEL_DISCOVERY": "0"}), \
             patch.object(api_config, "requests", SimpleNamespace(get=fake_get)):
            api_config.refresh_dynamic_models("local")
            local_models = api_config.api_providers()["local"]["models"]

        self.assertIn("DeepSeek R1 8B (Ollama)", local_models)
        self.assertIn("Gemma 3 12B IT (LM Studio)", local_models)
        self.assertIn("llama3.1:8b (Ollama)", local_models)
        self.assertNotIn("Qwen 3 8B (Ollama)", local_models)
        self.assertEqual(
            local_models["llama3.1:8b (Ollama)"]["base_url"],
            "http://127.0.0.1:11434/v1/chat/completions",
        )
        self.assertNotIn("max_output_tokens", local_models["llama3.1:8b (Ollama)"])
        self.assertNotIn("max_output_tokens", local_models["DeepSeek R1 8B (Ollama)"])
        self.assertEqual(
            local_models["Gemma 3 12B IT (LM Studio)"]["context_length"],
            262144,
        )
        self.assertNotIn("max_output_tokens", local_models["Gemma 3 12B IT (LM Studio)"])

    def test_refresh_dynamic_models_falls_back_to_static_config_when_servers_unreachable(self):
        def fake_get(url, timeout=None):
            raise OSError(f"Connection failed for {url}")

        with patch.dict(os.environ, {"GT_DISABLE_LOCAL_MODEL_DISCOVERY": "0"}), \
             patch.object(api_config, "requests", SimpleNamespace(get=fake_get)):
            api_config.refresh_dynamic_models("local")
            local_models = api_config.api_providers()["local"]["models"]

        self.assertIn("Qwen 3 8B (Ollama)", local_models)
        self.assertIn("Gemma 3 12B IT (LM Studio)", local_models)
        self.assertNotIn("max_output_tokens", local_models["Qwen 3 8B (Ollama)"])
        self.assertNotIn("max_output_tokens", local_models["Gemma 3 12B IT (LM Studio)"])


if __name__ == "__main__":
    unittest.main()
