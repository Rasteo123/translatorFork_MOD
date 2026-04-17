import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from gemini_translator.ui.dialogs.misc import EnhancedProjectHistoryDialog
from gemini_translator.utils.settings import SettingsManager


class _HistorySettingsStub:
    def __init__(self, projects_root_folder=""):
        self.projects_root_folder = projects_root_folder
        self.saved_history = None

    def get_last_projects_root_folder(self):
        return self.projects_root_folder

    def save_last_projects_root_folder(self, folder_path):
        self.projects_root_folder = folder_path

    def save_project_history(self, history_list):
        self.saved_history = list(history_list)
        return True


class ProjectHistoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_save_project_history_deduplicates_normalized_output_folder(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_file = os.path.join(temp_dir, "settings.json")
            manager = SettingsManager(config_file=config_file)

            output_folder = os.path.join(temp_dir, "Project One")
            epub_path = os.path.join(output_folder, "book.epub")
            output_folder_variant = output_folder.replace("\\", "/")
            epub_path_variant = epub_path.replace("\\", "/")

            manager.save_project_history([
                {
                    "name": "Recent Entry",
                    "epub_path": epub_path_variant,
                    "output_folder": output_folder_variant,
                },
                {
                    "name": "Duplicate Entry",
                    "epub_path": epub_path,
                    "output_folder": output_folder,
                },
            ])

            history = manager.load_project_history()

            self.assertEqual(len(history), 1)
            self.assertEqual(history[0]["name"], "Recent Entry")
            self.assertEqual(history[0]["output_folder"], os.path.normpath(output_folder))
            self.assertEqual(history[0]["epub_path"], os.path.normpath(epub_path))

    def test_remove_history_project_hides_scanned_entry_in_current_dialog(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_folder = os.path.join(temp_dir, "Project Two")
            os.makedirs(project_folder, exist_ok=True)
            with open(os.path.join(project_folder, "translation_map.json"), "w", encoding="utf-8") as fh:
                fh.write("{}")

            history = [{
                "name": "Project Two",
                "epub_path": os.path.join(project_folder, "book.epub"),
                "output_folder": project_folder,
            }]
            settings_stub = _HistorySettingsStub(projects_root_folder=temp_dir)

            dialog = EnhancedProjectHistoryDialog(history, settings_manager=settings_stub)
            dialog._refresh_project_list()

            self.assertEqual(len(dialog.all_projects), 1)
            self.assertTrue(dialog.all_projects[0]["_from_history"])
            self.assertTrue(dialog.all_projects[0]["_from_scan"])

            removed = dialog._remove_history_projects([dialog.all_projects[0]])

            self.assertTrue(removed)
            self.assertEqual(settings_stub.saved_history, [])
            self.assertEqual(dialog.list_widget.count(), 0)
            self.assertEqual(dialog.all_projects, [])

            dialog.hidden_removed_folder_keys.clear()
            dialog._refresh_project_list()

            self.assertEqual(len(dialog.all_projects), 1)
            self.assertFalse(dialog.all_projects[0]["_from_history"])
            self.assertTrue(dialog.all_projects[0]["_from_scan"])
            dialog.close()


if __name__ == "__main__":
    unittest.main()
