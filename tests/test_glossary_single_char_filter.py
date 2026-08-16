# -*- coding: utf-8 -*-

from gemini_translator.utils.language_tools import SmartGlossaryFilter


def _filter(glossary, text, threshold=90):
    return SmartGlossaryFilter().filter_glossary_for_text(
        full_glossary=glossary,
        text=text,
        fuzzy_threshold=threshold,
        use_jieba_for_glossary_search=True,
    )


NAMES_GLOSSARY = {
    '唐元': {'rus': 'Тан Юань', 'note': 'Персонаж; Мужчина'},
    '唐': {'rus': 'Тан', 'note': 'Фамилия'},
    '武魂殿': {'rus': 'Зал Духов', 'note': 'Организация'},
    '武魂': {'rus': 'боевой дух', 'note': 'Термин'},
    '武': {'rus': 'боевой', 'note': 'Морфема'},
}


class TestSingleCharSubtermFiltering:
    def test_single_char_inside_longer_term_is_not_pulled_in(self):
        text = "唐元走進院子，看著玉天恆。"
        found = _filter(NAMES_GLOSSARY, text)
        assert '唐元' in found
        assert '唐' not in found

    def test_single_char_with_standalone_occurrence_is_kept(self):
        # 唐 встречается отдельно (династия Тан), а не только внутри имени.
        text = "唐元走進院子。這是唐的舊事。"
        found = _filter(NAMES_GLOSSARY, text)
        assert '唐元' in found
        assert '唐' in found

    def test_multi_char_subterm_is_still_pulled_in(self):
        text = "武魂殿的人來了。"
        found = _filter(NAMES_GLOSSARY, text)
        assert '武魂殿' in found
        assert '武魂' in found, "многосимвольные подтермины должны сохраняться"
        assert '武' not in found

    def test_longest_match_itself_is_never_dropped(self):
        single_char_only = {'武': {'rus': 'боевой', 'note': 'Морфема'}}
        found = _filter(single_char_only, "他的武很強。")
        assert '武' in found, "самостоятельное вхождение должно сохраняться"

    def test_strict_threshold_behaviour_unchanged(self):
        text = "唐元走進院子。"
        found = _filter(NAMES_GLOSSARY, text, threshold=100)
        assert '唐元' in found
        assert '唐' not in found

    def test_empty_inputs_are_safe(self):
        assert _filter(NAMES_GLOSSARY, "") == {}
        assert _filter({}, "唐元") == {}
