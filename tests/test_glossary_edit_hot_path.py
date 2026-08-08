import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("GT_DISABLE_LOCAL_MODEL_DISCOVERY", "1")

from PyQt6 import QtWidgets

from main import EventBus
from gemini_translator.ui.widgets.glossary_widget import GlossaryWidget  # noqa: F401
from gemini_translator.ui.dialogs.glossary import GlossaryManagerPage
from gemini_translator.utils.settings import SettingsManager


class GlossaryEditHotPathTests(unittest.TestCase):
    """Правка одной ячейки не должна вызывать каскад полных чтений глоссария.

    Исторически одна правка делала ~5 полных SELECT всей таблицы: компаратор
    несохранённых изменений в add_history, get_glossary в _run_full_analysis
    и по одному в каждом из трёх вызовов _update_analysis_widgets.
    """

    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.app.event_bus = EventBus()
        self.settings_file = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
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

    def _make_page(self):
        page = GlossaryManagerPage(mode="child")

        def cleanup():
            page.close()
            page.deleteLater()
            self.app.processEvents()

        self.addCleanup(cleanup)
        return page

    def test_cell_edit_does_at_most_one_full_glossary_read(self):
        page = self._make_page()
        page.set_glossary(
            [{"original": f"term{i}", "rus": f"перевод{i}", "note": ""}
             for i in range(10)],
            run_analysis=False,
        )

        reads = []
        original_get = page.get_glossary
        page.get_glossary = lambda: (reads.append(1) or original_get())

        item = page.table.item(0, 1)
        self.assertIsNotNone(item, "Таблица не заполнена после set_glossary")
        page.table.blockSignals(True)
        item.setText("новый перевод")
        page.table.blockSignals(False)

        page._update_db_from_item(item)

        self.assertLessEqual(
            len(reads), 1,
            f"Правка одной ячейки сделала {len(reads)} полных чтений глоссария",
        )

    def test_analysis_snapshot_is_isolated_from_live_state(self):
        page = self._make_page()
        page.direct_conflicts = {"т": [{"rus": "а", "note": ""}]}
        page.reverse_issues = {"п": {"complete": [{"original": "т"}], "orphans": []}}
        page.overlap_groups = {"т": ["тт"]}
        page.inverted_overlaps = {"тт": ["т"]}
        page.conflicting_term_keys = {"т"}
        page.conflict_map["т"].add("direct")
        page.term_to_conflict_keys_map["т"]["direct_conflicts"].add("т")

        snapshot = page._get_analysis_snapshot()

        page.direct_conflicts["т"].append({"rus": "б", "note": ""})
        page.direct_conflicts["т"][0]["rus"] = "изменено"
        page.reverse_issues["п"]["complete"][0]["original"] = "другое"
        page.reverse_issues["п"]["orphans"].append({"original": ""})
        page.overlap_groups["т"].append("ттт")
        page.inverted_overlaps["тт"].append("х")
        page.conflict_map["т"].add("overlap")
        page.term_to_conflict_keys_map["т"]["direct_conflicts"].add("т2")
        page.conflicting_term_keys.add("т2")

        self.assertEqual(snapshot["direct_conflicts"], {"т": [{"rus": "а", "note": ""}]})
        self.assertEqual(
            snapshot["reverse_issues"],
            {"п": {"complete": [{"original": "т"}], "orphans": []}},
        )
        self.assertEqual(snapshot["overlap_groups"], {"т": ["тт"]})
        self.assertEqual(snapshot["inverted_overlaps"], {"тт": ["т"]})
        self.assertEqual(snapshot["conflict_map"]["т"], {"direct"})
        self.assertEqual(
            snapshot["term_to_conflict_keys_map"]["т"]["direct_conflicts"], {"т"}
        )
        self.assertEqual(snapshot["conflicting_term_keys"], {"т"})

        # После restore структуры должны сохранить defaultdict-семантику,
        # на неё полагается _rebuild_conflict_maps.
        page._restore_analysis_snapshot(snapshot)
        page.conflict_map["новый"].add("x")
        page.term_to_conflict_keys_map["новый"]["direct_conflicts"].add("y")


if __name__ == "__main__":
    unittest.main()
