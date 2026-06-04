import unittest
from unittest.mock import MagicMock

from gemini_translator.api import config as api_config
from gemini_translator.core.translation_engine import TranslationEngine


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
        http = [k for k, v in providers.items()
                if not api_config.uses_legacy_worker_thread(v)]
        self.assertTrue(http, "expected >=1 runtime (HTTP) provider")


class DispatchRoutingTests(unittest.TestCase):
    def _engine(self):
        eng = TranslationEngine.__new__(TranslationEngine)
        eng.executor = MagicMock()
        eng.runtime = MagicMock()
        eng.active_workers_map = {}
        eng.keys_map = {}
        return eng

    def test_subprocess_provider_routes_to_executor(self):
        eng = self._engine()
        eng._spawn_worker({"worker_runtime": "thread"}, worker=MagicMock())
        eng.executor.submit.assert_called_once()
        eng.runtime.spawn.assert_not_called()

    def test_http_provider_routes_to_runtime(self):
        eng = self._engine()
        eng._spawn_worker({"is_async": True}, worker=MagicMock())
        eng.runtime.spawn.assert_called_once()
        eng.executor.submit.assert_not_called()

    def test_runtime_worker_gets_sync_executor(self):
        eng = self._engine()
        eng.runtime.sync_executor = object()
        worker = MagicMock()
        eng._spawn_worker({"is_async": True}, worker=worker)
        self.assertIs(worker.sync_executor, eng.runtime.sync_executor)


class MixedShutdownTests(unittest.TestCase):
    def test_terminate_stops_runtime_then_executor_and_clears_map(self):
        eng = TranslationEngine.__new__(TranslationEngine)
        runtime, executor = MagicMock(), MagicMock()   # capture before terminate nulls them
        eng.executor = executor
        eng.runtime = runtime
        eng.active_workers_map = {"w1": MagicMock()}
        eng.bus = MagicMock()
        eng._post_event = lambda *a, **k: None
        eng._terminate_all_workers()
        runtime.stop.assert_called_once()
        executor.shutdown.assert_called()
        self.assertEqual(eng.active_workers_map, {})


class WorkerFinishBookkeepingTests(unittest.TestCase):
    """_on_worker_finished must release the key + drop the worker uniformly,
    regardless of whether the future came from runtime.spawn or executor.submit
    (both are concurrent.futures.Future)."""

    def _engine(self):
        eng = TranslationEngine.__new__(TranslationEngine)
        eng.task_manager = MagicMock()
        eng.api_key_manager = MagicMock()
        eng.active_workers_map = {}
        eng.keys_map = {}
        eng.shutting_down_workers = set()
        eng._post_event = lambda *a, **k: None
        eng._try_launch_replacement = lambda: False
        eng._check_if_session_finished = lambda: None
        return eng

    def test_finish_releases_key_and_removes_worker(self):
        from concurrent.futures import Future
        eng = self._engine()
        fut = Future()
        fut.set_result(None)                 # same type either dispatch path returns
        eng.active_workers_map["w1"] = fut
        eng.keys_map["w1"] = "KEY1"
        eng.keys_map["KEY1"] = "w1"
        eng._on_worker_finished("w1", fut)
        self.assertNotIn("w1", eng.active_workers_map)
        eng.api_key_manager.release_key.assert_called_with("KEY1")


if __name__ == "__main__":
    unittest.main()
