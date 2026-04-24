import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import gemini_reader_v3 as reader


class _VoiceKeyHarness:
    _reader_key_model_quota_exhausted = reader.MainWindow._reader_key_model_quota_exhausted
    _reader_key_has_request_budget = reader.MainWindow._reader_key_has_request_budget
    _voice_test_request_amount = reader.MainWindow._voice_test_request_amount
    _select_voice_test_api_key = reader.MainWindow._select_voice_test_api_key

    def __init__(self, available_keys, request_counts, limit=10):
        self.available_keys = list(available_keys)
        self.request_counts = dict(request_counts)
        self.limit = limit

    def _get_available_api_keys(self, required_model_ids=None):
        return list(self.available_keys)

    def _reader_request_limit_for_model(self, model_id):
        return self.limit

    def _reader_request_count_for_key(self, api_key, model_id):
        return self.request_counts.get(api_key, 0)


class ReaderVoiceSampleKeySelectionTests(unittest.TestCase):
    def test_voice_test_uses_available_key_instead_of_first_saved_key(self):
        harness = _VoiceKeyHarness(
            available_keys=["ready-key"],
            request_counts={"limited-first-key": 10, "ready-key": 0},
        )

        self.assertEqual(
            harness._select_voice_test_api_key("gemini-3.1-flash-tts-preview", "flash_tts", "single"),
            "ready-key",
        )

    def test_author_gender_voice_test_requires_three_remaining_requests(self):
        harness = _VoiceKeyHarness(
            available_keys=["almost-spent-key", "ready-key"],
            request_counts={"almost-spent-key": 8, "ready-key": 0},
        )

        self.assertEqual(
            harness._select_voice_test_api_key("gemini-3.1-flash-live-preview", "live", "author_gender"),
            "ready-key",
        )

    def test_returns_empty_when_available_keys_do_not_have_enough_budget(self):
        harness = _VoiceKeyHarness(
            available_keys=["almost-spent-key"],
            request_counts={"almost-spent-key": 8},
        )

        self.assertEqual(
            harness._select_voice_test_api_key("gemini-3.1-flash-live-preview", "live", "author_gender"),
            "",
        )


if __name__ == "__main__":
    unittest.main()
