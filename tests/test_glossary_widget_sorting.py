import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from gemini_translator.ui.widgets.glossary_widget import GlossaryWidget, sorted_glossary_entries


class GlossaryWidgetSortingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_sorted_glossary_entries_orders_by_original_and_keeps_blank_last(self):
        entries = [
            {"original": "zeta", "rus": "zeta"},
            {"original": "", "rus": "blank"},
            {"original": "Alpha", "rus": "alpha"},
            {"original": "beta", "rus": "beta"},
        ]

        sorted_entries = sorted_glossary_entries(entries)

        self.assertEqual(
            [entry["original"] for entry in sorted_entries],
            ["Alpha", "beta", "zeta", ""],
        )

    def test_set_glossary_resets_main_widget_to_first_page(self):
        widget = GlossaryWidget()
        self.addCleanup(widget.close)
        widget.items_per_page = 2
        widget.current_page = 1

        widget.set_glossary([
            {"original": "delta", "rus": "delta"},
            {"original": "Charlie", "rus": "charlie"},
            {"original": "bravo", "rus": "bravo"},
            {"original": "Alpha", "rus": "alpha"},
        ])

        self.assertEqual(widget.current_page, 0)
        self.assertEqual(widget.table.rowCount(), 2)
        self.assertEqual(widget.table.item(0, 0).text(), "Alpha")
        self.assertEqual(widget.table.item(1, 0).text(), "bravo")


if __name__ == "__main__":
    unittest.main()
