import unittest

from gemini_translator.utils.glossary_tools import ContextManager
from gemini_translator.utils.language_tools import (
    JIEBA_AVAILABLE,
    OPENCC_AVAILABLE,
    ChineseTextProcessor,
    GlossaryReplacer,
    GlossaryRegexService,
    SmartGlossaryFilter,
    get_chinese_script_variants,
)


TRAD_TERM = "\u96f2\u5922\u6c5f\u6c0f"
SIMP_TERM = "\u4e91\u68a6\u6c5f\u6c0f"
TRANSLATION = "Yunmeng Jiang clan"


@unittest.skipUnless(OPENCC_AVAILABLE, "opencc is required for Chinese script conversion")
class ChineseScriptGlossaryTests(unittest.TestCase):
    def test_variants_include_simplified_and_traditional_forms(self):
        self.assertIn(SIMP_TERM, get_chinese_script_variants(TRAD_TERM))
        self.assertIn(TRAD_TERM, get_chinese_script_variants(SIMP_TERM))

    def test_regex_service_matches_traditional_glossary_in_simplified_text(self):
        glossary = {TRAD_TERM: {"rus": TRANSLATION}}

        matches = GlossaryRegexService(glossary).find_matches(f"<p>{SIMP_TERM}</p>")

        self.assertEqual({TRAD_TERM}, matches)

    def test_dynamic_filter_matches_both_script_directions(self):
        filter_instance = SmartGlossaryFilter()

        trad_glossary = {TRAD_TERM: {"rus": TRANSLATION}}
        found_from_simplified = filter_instance.filter_glossary_for_text(
            trad_glossary,
            f"<p>{SIMP_TERM}</p>",
            fuzzy_threshold=100,
            use_jieba_for_glossary_search=False,
        )

        simplified_glossary = {SIMP_TERM: {"rus": TRANSLATION}}
        found_from_traditional = filter_instance.filter_glossary_for_text(
            simplified_glossary,
            f"<p>{TRAD_TERM}</p>",
            fuzzy_threshold=100,
            use_jieba_for_glossary_search=False,
        )

        self.assertIn(TRAD_TERM, found_from_simplified)
        self.assertIn(SIMP_TERM, found_from_traditional)

    def test_prompt_uses_source_script_variant_present_in_text(self):
        manager = ContextManager(output_folder="", use_jieba=False)
        manager.update_settings(
            {
                "full_glossary_data": {TRAD_TERM: {"rus": TRANSLATION}},
                "dynamic_glossary": True,
                "use_jieba": False,
                "fuzzy_threshold": 100,
            }
        )

        glossary_prompt = manager.format_glossary_for_prompt(f"<p>{SIMP_TERM}</p>")

        self.assertIn(f'"s": "{SIMP_TERM}"', glossary_prompt)
        self.assertNotIn(f'"s": "{TRAD_TERM}"', glossary_prompt)

    @unittest.skipUnless(JIEBA_AVAILABLE, "jieba is required for strict CJK validation")
    def test_jieba_validation_accepts_trained_script_variant(self):
        glossary = {TRAD_TERM: {"rus": TRANSLATION}}
        processor = ChineseTextProcessor()

        try:
            processor.add_custom_words(glossary)
            found = SmartGlossaryFilter(processor).filter_glossary_for_text(
                glossary,
                f"<p>{SIMP_TERM}</p>",
                fuzzy_threshold=100,
                use_jieba_for_glossary_search=True,
            )
        finally:
            processor.reset()

        self.assertIn(TRAD_TERM, found)

    @unittest.skipUnless(JIEBA_AVAILABLE, "jieba is required for glossary replacement")
    def test_replacer_uses_script_variants_for_html_replacement(self):
        replacer = GlossaryReplacer({TRAD_TERM: {"rus": TRANSLATION}})

        try:
            replacer.prepare()
            replaced = replacer.process_html(f"<p>{SIMP_TERM}</p>")
        finally:
            replacer.cleanup()

        self.assertIn(TRANSLATION, replaced)


if __name__ == "__main__":
    unittest.main()
