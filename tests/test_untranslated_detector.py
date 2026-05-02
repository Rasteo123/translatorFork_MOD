import unittest

from gemini_translator.ui.dialogs.validation_dialogs.untranslated_detector import (
    UntranslatedWordDetector,
)


class UntranslatedDetectorTests(unittest.TestCase):
    def test_cjk_punctuation_is_not_reported_as_mixed_script(self):
        detector = UntranslatedWordDetector(set())

        issues = detector.detect_mixed_script(
            "<p>Принудительная любовь】Безумие。Сверхсладко</p>"
        )

        self.assertEqual(issues, [])

    def test_cjk_text_is_still_reported_near_cyrillic(self):
        detector = UntranslatedWordDetector(set())

        issues = detector.detect_mixed_script("<p>Он увидел 魔法 рядом.</p>")

        self.assertTrue(any(item["text"] == "魔" for item in issues))
        self.assertTrue(any(item["text"] == "法" for item in issues))

    def test_cjk_punctuation_is_not_reported_as_untranslated_word(self):
        detector = UntranslatedWordDetector(set())

        self.assertFalse(detector._should_include_word("。"))
        self.assertFalse(detector._should_include_word("】"))


if __name__ == "__main__":
    unittest.main()
