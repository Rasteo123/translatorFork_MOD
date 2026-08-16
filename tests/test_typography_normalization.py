# -*- coding: utf-8 -*-

import pytest

from gemini_translator.utils.text import (
    normalize_chapter_heading_format,
    normalize_dialogue_dashes,
)


class TestNormalizeDialogueDashes:
    def test_leading_em_dash_becomes_speech_operator(self):
        html = "<p>— Кто этот малыш?</p>"
        assert normalize_dialogue_dashes(html) == "<p>─ Кто этот малыш?</p>"

    def test_attribution_boundary_inside_speech_becomes_operator(self):
        html = "<p>— Буря идет, — сказал он.</p>"
        assert normalize_dialogue_dashes(html) == "<p>─ Буря идет, ─ сказал он.</p>"

    def test_em_dash_in_narration_becomes_en_dash(self):
        html = "<p>Ветер усилился — буря приближалась.</p>"
        assert normalize_dialogue_dashes(html) == "<p>Ветер усилился – буря приближалась.</p>"

    def test_internal_pause_inside_speech_becomes_en_dash(self):
        html = "<p>─ Я подумал — и решил остаться.</p>"
        assert normalize_dialogue_dashes(html) == "<p>─ Я подумал – и решил остаться.</p>"

    def test_existing_operators_and_en_dashes_are_untouched(self):
        html = "<p>─ Идем, ─ сказал он. Ветер усилился – стало холодно.</p>"
        assert normalize_dialogue_dashes(html) == html

    def test_html_comments_and_attributes_are_untouched(self):
        html = '<!-- MEDIA_0 --><p class="a—b">Текст — текст</p>'
        result = normalize_dialogue_dashes(html)
        assert "<!-- MEDIA_0 -->" in result
        assert 'class="a—b"' in result
        assert "Текст – текст" in result

    def test_inline_tags_do_not_break_paragraph_context(self):
        html = "<p><em>—</em> Идем, — сказал он.</p>"
        result = normalize_dialogue_dashes(html)
        assert "<em>─</em>" in result
        assert "─ сказал он." in result

    def test_non_string_input_returns_input(self):
        assert normalize_dialogue_dashes(None) is None


class TestNormalizeChapterHeadingFormat:
    def test_missing_guillemets_are_added(self):
        html = "<h1>Глава 129: Кость правой руки</h1>"
        assert normalize_chapter_heading_format(html) == "<h1>Глава 129: «Кость правой руки»</h1>"

    def test_inner_guillemets_become_nested_quotes(self):
        html = "<h1>Глава 59: Дуэт «Милашки»</h1>"
        assert normalize_chapter_heading_format(html) == "<h1>Глава 59: «Дуэт „Милашки“»</h1>"

    def test_correct_heading_is_untouched(self):
        html = "<h1>Глава 432: «Храм в звездном небе»</h1>"
        assert normalize_chapter_heading_format(html) == html

    def test_heading_without_number_keeps_that_shape(self):
        html = "<h1>Глава: «Совет в Хэхае»</h1>"
        assert normalize_chapter_heading_format(html) == html

    def test_number_without_colon_is_normalized(self):
        html = "<h1>Глава 12 Начало пути</h1>"
        assert normalize_chapter_heading_format(html) == "<h1>Глава 12: «Начало пути»</h1>"

    def test_untranslated_cjk_heading_is_left_alone(self):
        html = "<h1>《穿越鬥羅之生死簿》第432章 星空中的神殿</h1>"
        assert normalize_chapter_heading_format(html) == html

    def test_non_chapter_heading_is_left_alone(self):
        html = "<h1>Пролог</h1>"
        assert normalize_chapter_heading_format(html) == html

    def test_only_first_heading_is_normalized(self):
        html = "<h1>Глава 5: Путь</h1><h2>Глава 6: Второй</h2>"
        result = normalize_chapter_heading_format(html)
        assert "«Путь»" in result
        assert "<h2>Глава 6: Второй</h2>" in result

    @pytest.mark.parametrize("value", [None, 123, ""])
    def test_non_string_input_returns_input(self, value):
        assert normalize_chapter_heading_format(value) == value
