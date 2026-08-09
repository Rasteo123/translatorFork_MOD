import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("GT_DISABLE_LOCAL_MODEL_DISCOVERY", "1")

from PyQt6 import QtCore, QtWidgets

from main import EventBus
from gemini_translator.ui.widgets.glossary_widget import GlossaryWidget  # noqa: F401
from gemini_translator.ui.dialogs.glossary import GlossaryManagerPage
from gemini_translator.ui.dialogs.glossary_dialogs.conflict_resolvers import (
    ReverseConflictResolverPage,
)
from gemini_translator.utils.language_tools import GlossaryLogic
from gemini_translator.utils.settings import SettingsManager


class GlossaryConflictIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.app.event_bus = EventBus()
        self.settings_file = tempfile.NamedTemporaryFile(
            suffix=".json",
            delete=False,
        )
        self.settings_file.close()
        self.settings = SettingsManager(
            event_bus=self.app.event_bus,
            config_file=self.settings_file.name,
        )
        self.app.settings_manager = self.settings
        self.app.get_settings_manager = lambda: self.settings
        self.app.global_version = ""

    def tearDown(self):
        self.settings.flush()
        try:
            os.unlink(self.settings_file.name)
        except FileNotFoundError:
            pass

    def test_exact_duplicates_are_direct_but_not_reverse_conflicts(self):
        glossary = [
            {"original": "Alpha", "rus": "Альфа", "note": ""},
            {"original": "Alpha", "rus": "Альфа", "note": ""},
        ]

        logic = GlossaryLogic()
        direct = logic.find_direct_conflicts(glossary)[1]
        reverse = logic.find_reverse_issues(glossary)

        self.assertEqual(
            direct,
            {
                "Alpha": [
                    {"rus": "Альфа", "note": ""},
                    {"rus": "Альфа", "note": ""},
                ]
            },
        )
        self.assertEqual(reverse, {})

    def test_reverse_resolver_deletes_only_the_selected_duplicate_row(self):
        manager = GlossaryManagerPage(mode="child")
        self.addCleanup(manager.close)
        manager.set_glossary(
            [
                {
                    "original": "Alpha",
                    "rus": "Общий перевод",
                    "note": "",
                    "timestamp": 101.0,
                },
                {
                    "original": "Alpha",
                    "rus": "Общий перевод",
                    "note": "",
                    "timestamp": 202.0,
                },
                {
                    "original": "Beta",
                    "rus": "Общий перевод",
                    "note": "",
                    "timestamp": 303.0,
                },
            ],
            run_analysis=False,
        )

        issues = manager.logic.find_reverse_issues(manager.get_glossary())
        resolver = ReverseConflictResolverPage(
            issues,
            manager._get_glossary_with_db_ids(),
        )
        self.addCleanup(resolver.close)

        self.assertEqual(resolver.complete_table.rowCount(), 3)
        second_row_id = resolver.complete_table.item(1, 0).data(
            QtCore.Qt.ItemDataRole.UserRole
        )
        self.assertEqual(resolver.entry_map[second_row_id]["timestamp"], 202.0)

        resolver._delete_entry(second_row_id)

        self.assertEqual(resolver.complete_table.rowCount(), 2)
        patch = resolver.get_patch()
        self.assertEqual(len(patch), 1)
        self.assertEqual(patch[0]["before"]["_db_id"], second_row_id)

        manager._apply_patch(patch)

        self.assertEqual(
            [entry["timestamp"] for entry in manager.get_glossary()],
            [101.0, 303.0],
        )


if __name__ == "__main__":
    unittest.main()
