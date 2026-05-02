import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets
from PyQt6.QtWidgets import QTextEdit

from gemini_translator.ui.dialogs.validation import TranslationValidatorDialog
from gemini_translator.ui.dialogs.validation_dialogs.untranslated_fixer_dialog import (
    UntranslatedFixerDialog,
)


class _FixerHarness:
    _pre_analyze_data = UntranslatedFixerDialog._pre_analyze_data
    _get_effective_context_payload = UntranslatedFixerDialog._get_effective_context_payload
    _collect_visible_candidates_for_item = UntranslatedFixerDialog._collect_visible_candidates_for_item
    _sort_visible_candidates = UntranslatedFixerDialog._sort_visible_candidates
    _build_chapter_navigation_payload = UntranslatedFixerDialog._build_chapter_navigation_payload


class _ValidatorHarness:
    _normalize_navigation_search_text = staticmethod(TranslationValidatorDialog._normalize_navigation_search_text)
    _navigation_search_candidates = TranslationValidatorDialog._navigation_search_candidates
    _find_problem_text_in_widget = TranslationValidatorDialog._find_problem_text_in_widget


class UntranslatedFixerNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _build_dialog_stub(self, item):
        dialog = _FixerHarness()
        dialog.original_data = [item]
        dialog.blacklist_set = set()
        dialog.whitelist_set = set()
        dialog._pre_analyze_data()
        return dialog

    def test_symbol_term_is_available_as_filter_candidate(self):
        item = {
            "term": "】",
            "context": "<p>Текст со сломанным списком 】</p>",
            "location_info": "Text/chapter1.xhtml",
            "source_type": "system",
        }
        dialog = self._build_dialog_stub(item)

        payload = dialog._collect_visible_candidates_for_item(item)

        self.assertIn("】", payload["remaining_candidates"])
        self.assertEqual(item["lang_tag"], "other")

    def test_context_symbol_is_available_even_when_term_is_word(self):
        item = {
            "term": "Level",
            "context": "<p>Level 10 】 。 рядом с пунктом списка</p>",
            "location_info": "Text/chapter1.xhtml",
            "source_type": "system",
        }
        dialog = self._build_dialog_stub(item)

        payload = dialog._collect_visible_candidates_for_item(item)

        self.assertIn("Level", payload["remaining_candidates"])
        self.assertIn("】", payload["remaining_candidates"])
        self.assertIn("。", payload["remaining_candidates"])

    def test_problem_symbols_are_prioritized_before_long_word_list(self):
        item = {
            "term": "Level",
            "context": (
                "<p>Alpha Bravo Charlie Delta Echo Foxtrot Golf Hotel India "
                "Juliet Kilo Lima Mike November Oscar Papa Quebec 。</p>"
            ),
            "location_info": "Text/chapter1.xhtml",
            "source_type": "system",
        }
        dialog = self._build_dialog_stub(item)

        payload = dialog._collect_visible_candidates_for_item(item)
        sorted_candidates = dialog._sort_visible_candidates(
            payload["remaining_candidates"],
            item["term"],
        )

        self.assertEqual(sorted_candidates[0], "Level")
        self.assertIn("。", sorted_candidates[:3])

    def test_symbol_term_can_be_hidden_by_blacklist(self):
        item = {
            "term": "】",
            "context": "<p>Текст со сломанным списком 】</p>",
            "location_info": "Text/chapter1.xhtml",
            "source_type": "system",
        }
        dialog = self._build_dialog_stub(item)
        dialog.blacklist_set.add("】")

        payload = dialog._collect_visible_candidates_for_item(item)

        self.assertNotIn("】", payload["remaining_candidates"])

    def test_navigation_payload_preserves_chapter_and_exact_html(self):
        item = {
            "term": "】",
            "context": "<p>Текст со сломанным списком 】</p>",
            "location_info": "Text/chapter1.xhtml",
            "source_type": "user",
            "internal_html_path": "Text/chapter1.xhtml",
            "occurrences": [
                {
                    "replace_mode": "literal_html",
                    "row_index": 3,
                    "literal_html": "<p>Текст со сломанным списком 】</p>",
                }
            ],
        }
        dialog = self._build_dialog_stub(item)

        payload = dialog._build_chapter_navigation_payload(0)

        self.assertEqual(payload["internal_html_path"], "Text/chapter1.xhtml")
        self.assertEqual(payload["term"], "】")
        self.assertEqual(payload["literal_html"], "<p>Текст со сломанным списком 】</p>")
        self.assertIn("】", payload["context_preview"])

    def test_validator_navigation_candidates_include_visible_and_raw_context(self):
        validator = _ValidatorHarness()
        payload = {
            "term": "】",
            "context": "<p>Текст со сломанным списком 】</p>",
            "context_preview": "",
            "literal_html": "<p>Текст со сломанным списком 】</p>",
        }

        visible = validator._navigation_search_candidates(payload, raw_html=False)
        raw = validator._navigation_search_candidates(payload, raw_html=True)

        self.assertIn("Текст со сломанным списком 】", visible)
        self.assertIn("】", visible)
        self.assertIn("<p>Текст со сломанным списком 】</p>", raw)

    def test_problem_text_search_handles_symbol_candidate(self):
        validator = _ValidatorHarness()
        text_edit = QTextEdit()
        text_edit.setPlainText("До\nТекст со сломанным списком 】\nПосле")

        found = validator._find_problem_text_in_widget(
            text_edit,
            ["Текст со сломанным списком 】"],
        )

        self.assertTrue(found)
        self.assertIn("】", text_edit.textCursor().selectedText())


if __name__ == "__main__":
    unittest.main()
