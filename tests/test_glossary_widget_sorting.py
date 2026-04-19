import unittest

from gemini_translator.ui.widgets.glossary_widget import sorted_glossary_entries


class GlossaryWidgetSortingTests(unittest.TestCase):
    def test_sorted_glossary_entries_orders_by_original_and_keeps_blank_last(self):
        entries = [
            {"original": "zeta", "rus": "Зета"},
            {"original": "", "rus": "Пусто"},
            {"original": "Alpha", "rus": "Альфа"},
            {"original": "beta", "rus": "Бета"},
        ]

        sorted_entries = sorted_glossary_entries(entries)

        self.assertEqual(
            [entry["original"] for entry in sorted_entries],
            ["Alpha", "beta", "zeta", ""],
        )


if __name__ == "__main__":
    unittest.main()
