import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import gemini_reader_v3 as reader


class _RuntimeKeyHarness:
    _api_key_required_model_ids = reader.MainWindow._api_key_required_model_ids
    _runtime_keys_for_required_models = reader.MainWindow._runtime_keys_for_required_models

    def __init__(self):
        self.seen_required_model_ids = None

    def _get_available_api_keys(self, required_model_ids=None):
        self.seen_required_model_ids = list(required_model_ids or [])
        return ["gemini-key"]


class ReaderChatGptPreprocessTests(unittest.TestCase):
    def test_preprocess_models_include_chatgpt_web(self):
        models_map = reader._build_preprocess_models_map()

        self.assertIn(reader.CHATGPT_WEB_PREPROCESS_MODEL_ID, set(models_map.values()))

    def test_chatgpt_web_prepare_uses_virtual_session_key(self):
        harness = _RuntimeKeyHarness()

        keys = harness._runtime_keys_for_required_models([reader.CHATGPT_WEB_PREPROCESS_MODEL_ID])

        self.assertEqual(keys, [reader._chatgpt_web_placeholder_api_key()])
        self.assertIsNone(harness.seen_required_model_ids)

    def test_chatgpt_web_auto_keeps_tts_api_key_requirement(self):
        harness = _RuntimeKeyHarness()

        keys = harness._runtime_keys_for_required_models(
            [reader.CHATGPT_WEB_PREPROCESS_MODEL_ID, "gemini-3.1-flash-tts-preview"]
        )

        self.assertEqual(keys, ["gemini-key"])
        self.assertEqual(harness.seen_required_model_ids, ["gemini-3.1-flash-tts-preview"])

    def test_chatgpt_web_requests_proactor_loop_on_windows(self):
        class DummyProactorLoop:
            pass

        with unittest.mock.patch.object(reader.sys, "platform", "win32"), unittest.mock.patch.object(
            reader.asyncio,
            "ProactorEventLoop",
            DummyProactorLoop,
            create=True,
        ):
            loop = reader._new_reader_event_loop(require_subprocess=True)

        self.assertIsInstance(loop, DummyProactorLoop)


if __name__ == "__main__":
    unittest.main()
