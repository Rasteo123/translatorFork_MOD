import json
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from gemini_translator.api import config as api_config


CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "api_providers.json"


class _DummyResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class OmniRouteProviderTests(unittest.TestCase):
    def setUp(self):
        api_config.initialize_configs()

    def tearDown(self):
        api_config.initialize_configs()

    def test_provider_is_visible_and_uses_translation_safe_headers(self):
        providers = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        provider = providers["omniroute"]

        self.assertTrue(provider["visible"])
        self.assertTrue(provider["dynamic_model_discovery"])
        self.assertEqual(provider["handler_class"], "OpenRouterApiHandler")
        self.assertEqual(
            provider["base_url"],
            "http://127.0.0.1:20128/v1/chat/completions",
        )
        self.assertEqual(
            provider["discovery_endpoints"],
            [{"name": "OmniRoute", "base_url": "http://127.0.0.1:20128/v1"}],
        )
        self.assertEqual(
            provider["extra_headers"],
            {
                "X-OmniRoute-No-Cache": "true",
                "x-omniroute-no-memory": "true",
                "x-omniroute-compression": "off",
            },
        )
        self.assertEqual(
            {model["id"] for model in provider["models"].values()},
            {"auto", "auto/offline", "auto/smart", "auto/fast", "auto/cheap"},
        )

    def test_dynamic_discovery_reads_omniroute_openai_catalog(self):
        def fake_get(url, timeout=None):
            if url == "http://127.0.0.1:20128/api/tags":
                return _DummyResponse(404, {})
            if url == "http://127.0.0.1:20128/v1/models":
                return _DummyResponse(
                    200,
                    {
                        "data": [
                            {"id": "auto"},
                            {"id": "mistral/mistral-small-latest"},
                        ]
                    },
                )
            if url == "http://127.0.0.1:20128/api/v0/models":
                return _DummyResponse(404, {})
            raise AssertionError(f"Unexpected discovery URL: {url}")

        with patch.dict(os.environ, {"GT_DISABLE_LOCAL_MODEL_DISCOVERY": "0"}), patch.object(
            api_config,
            "requests",
            SimpleNamespace(get=fake_get),
        ):
            api_config.ensure_dynamic_provider_models("omniroute")
            models = api_config.api_providers()["omniroute"]["models"]

        self.assertIn("OmniRoute Auto", models)
        self.assertIn("mistral/mistral-small-latest (OmniRoute)", models)
        self.assertEqual(
            models["OmniRoute Auto"]["base_url"],
            "http://127.0.0.1:20128/v1/chat/completions",
        )
        self.assertEqual(
            models["mistral/mistral-small-latest (OmniRoute)"]["id"],
            "mistral/mistral-small-latest",
        )


if __name__ == "__main__":
    unittest.main()
