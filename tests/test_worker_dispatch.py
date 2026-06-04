import unittest

from gemini_translator.api import config as api_config


class LegacyWorkerPredicateTests(unittest.TestCase):
    def test_subprocess_handlers_use_legacy_thread(self):
        self.assertTrue(
            api_config.uses_legacy_worker_thread({"worker_runtime": "thread"})
        )

    def test_http_handlers_use_runtime(self):
        self.assertFalse(api_config.uses_legacy_worker_thread({"is_async": True}))
        self.assertFalse(api_config.uses_legacy_worker_thread({}))

    def test_real_config_routing(self):
        import json
        import pathlib
        cfg = json.loads(
            (pathlib.Path(__file__).resolve().parents[1]
             / "config" / "api_providers.json").read_text()
        )
        providers = {k: v for k, v in cfg.items()
                     if isinstance(v, dict) and "handler_class" in v}
        for pid in ("workascii_chatgpt", "web_chatgpt_free", "web_perplexity"):
            self.assertIn(pid, providers, f"{pid} missing from config")
            self.assertTrue(api_config.uses_legacy_worker_thread(providers[pid]), pid)
        # At least one HTTP provider must route to the runtime (not legacy).
        http = [k for k, v in providers.items()
                if not api_config.uses_legacy_worker_thread(v)]
        self.assertTrue(http, "expected >=1 runtime (HTTP) provider")


if __name__ == "__main__":
    unittest.main()
