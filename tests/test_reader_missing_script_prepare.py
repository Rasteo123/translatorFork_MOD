import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import gemini_reader_v3 as reader


class _BookWithScripts:
    def __init__(self, scripts):
        self.scripts = scripts

    def load_tts_script(self, chapter_index):
        return self.scripts.get(chapter_index, "")


class _Check:
    def isChecked(self):
        return False


class _StatusBar:
    def __init__(self):
        self.messages = []

    def showMessage(self, message):
        self.messages.append(message)


class _PrepareMissingHarness:
    _chapter_has_tts_script_for_current_voice_mode = (
        reader.MainWindow._chapter_has_tts_script_for_current_voice_mode
    )
    _filter_indices_without_matching_script = reader.MainWindow._filter_indices_without_matching_script
    prepare_selected_scripts = reader.MainWindow.prepare_selected_scripts

    def __init__(self):
        self.bm = _BookWithScripts(
            {
                1: "Male: ready\nFemale: ready",
                2: "plain text for another mode",
            }
        )
        self.workers = []
        self.api_keys = ["gemini-key"]
        self.chk_selected_only = _Check()
        self.status_bar = _StatusBar()
        self.launched_indices = None

    def statusBar(self):
        return self.status_bar

    def _is_flash_tts_mode(self):
        return True

    def _selected_voice_mode(self):
        return "duo"

    def _selected_preprocess_uses_chatgpt_web(self):
        return False

    def _collect_run_scope_indices(self, include_done=False, action_label=""):
        return [0, 1, 2]

    def _launch_flash_workers(self, target_indices, run_mode):
        self.launched_indices = list(target_indices)
        self.launched_run_mode = run_mode


class ReaderMissingScriptPrepareTests(unittest.TestCase):
    def test_filter_indices_without_matching_script_uses_current_voice_mode(self):
        harness = _PrepareMissingHarness()

        missing = harness._filter_indices_without_matching_script([0, 1, 2])

        self.assertEqual(missing, [0, 2])

    def test_prepare_missing_scripts_launches_only_missing_chapters(self):
        harness = _PrepareMissingHarness()

        with mock.patch.object(reader, "genai", object()), mock.patch.object(reader, "genai_types", object()):
            harness.prepare_selected_scripts(only_missing=True)

        self.assertEqual(harness.launched_indices, [0, 2])
        self.assertEqual(harness.launched_run_mode, "prepare")
        self.assertIn("2", harness.status_bar.messages[-1])


if __name__ == "__main__":
    unittest.main()
