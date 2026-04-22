import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gemini_translator.utils.glossary_review import (
    classify_translation_review_change,
    normalize_translation_review_key,
)


class CorrectionPreviewDialogTests(unittest.TestCase):
    def test_normalized_equivalent_translation_key_ignores_wrappers_case_and_yo(self):
        normalized = normalize_translation_review_key(
            ' [\u00ab\u041f\u043e\u043b\u0443\u0434\u0401\u043c\u043e\u043d\u00bb] '
        )
        self.assertEqual(normalized, "\u043f\u043e\u043b\u0443\u0434\u0435\u043c\u043e\u043d")

    def test_classify_translation_change_treats_case_only_change_as_cosmetic(self):
        meaningful, cosmetic = classify_translation_review_change(
            ["\u041f\u043e\u043b\u0443\u0434\u0435\u043c\u043e\u043d"],
            "\u043f\u043e\u043b\u0443\u0434\u0435\u043c\u043e\u043d",
        )
        self.assertFalse(meaningful)
        self.assertTrue(cosmetic)

    def test_classify_translation_change_treats_wrappers_as_same_translation(self):
        meaningful, cosmetic = classify_translation_review_change(
            ["\u041f\u043e\u043b\u0443\u0434\u0435\u043c\u043e\u043d"],
            '["\u041f\u043e\u043b\u0443\u0434\u0435\u043c\u043e\u043d"]',
        )
        self.assertFalse(meaningful)
        self.assertTrue(cosmetic)

    def test_classify_translation_change_keeps_real_translation_change_visible(self):
        meaningful, cosmetic = classify_translation_review_change(
            ["\u041f\u043e\u043b\u0443\u0434\u0435\u043c\u043e\u043d"],
            "\u0412\u044b\u0441\u0448\u0438\u0439 \u0434\u0435\u043c\u043e\u043d",
        )
        self.assertTrue(meaningful)
        self.assertFalse(cosmetic)


if __name__ == "__main__":
    unittest.main()
