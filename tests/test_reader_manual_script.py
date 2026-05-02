import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import gemini_reader_v3 as reader


class _Chapter:
    def __init__(self, title="Chapter", raw_text="Raw text"):
        self.title = title
        self.raw_text = raw_text


class _Book:
    def __init__(self):
        self.chapters = [_Chapter("One"), _Chapter("Two")]
        self.saved = {}

    def save_tts_script(self, chapter_index, script_text):
        self.saved[chapter_index] = script_text
        return f"Ch{chapter_index + 1}.tts.txt"


class _TextView:
    def __init__(self):
        self.text = ""

    def setPlainText(self, text):
        self.text = text


class _Item:
    def __init__(self, chapter_index):
        self.chapter_index = chapter_index

    def data(self, _role):
        return self.chapter_index


class _ManualScriptHarness:
    _manual_script_target_index = reader.MainWindow._manual_script_target_index
    _save_manual_script_text = reader.MainWindow._save_manual_script_text
    _script_mode_mismatch_message = reader.MainWindow._script_mode_mismatch_message

    def __init__(self):
        self.bm = _Book()
        self.script_view = _TextView()
        self._current_chapter_index = None
        self.refreshed = 0
        self.voice_mode = "single"

    def refresh_chapters_list(self):
        self.refreshed += 1

    def _selected_chapter_indices(self):
        return [1]

    def _selected_voice_mode(self):
        return self.voice_mode


class ReaderManualScriptTests(unittest.TestCase):
    def test_manual_save_writes_script_and_refreshes_current_view(self):
        harness = _ManualScriptHarness()
        harness._current_chapter_index = 1

        saved_path = harness._save_manual_script_text(1, "Male: ready")

        self.assertEqual(saved_path, "Ch2.tts.txt")
        self.assertEqual(harness.bm.saved, {1: "Male: ready"})
        self.assertEqual(harness.refreshed, 1)
        self.assertEqual(harness.script_view.text, "Male: ready")

    def test_manual_target_prefers_context_item_then_current_then_selection(self):
        harness = _ManualScriptHarness()

        self.assertEqual(harness._manual_script_target_index([_Item(0)]), 0)

        harness._current_chapter_index = 0
        self.assertEqual(harness._manual_script_target_index(), 0)

        harness._current_chapter_index = None
        self.assertEqual(harness._manual_script_target_index(), 1)

    def test_mismatch_message_uses_current_voice_mode(self):
        harness = _ManualScriptHarness()
        harness.voice_mode = "duo"

        self.assertEqual(harness._script_mode_mismatch_message("Male: ready"), "")
        self.assertIn("Male", harness._script_mode_mismatch_message("plain text"))


if __name__ == "__main__":
    unittest.main()
