# -*- coding: utf-8 -*-
"""finding-ui-widgets-b_design_1-dup-compact-number-formatters.

Пять независимых копий «компактного форматирования числа» должны были
схлопнуться в два канонических хелпера в ``gemini_translator/utils/helpers.py``:

- ``format_compact_number`` (K/M-суффикс, пороги 1_000 / 1_000_000) — было
  ``StatusBarWidget._format_compact_number``,
  ``ConsistencyValidatorPage._format_compact_tokens``,
  ``UntranslatedFixerPage._format_compact_tokens``.
- ``format_thousands`` (разделитель тысяч — пробел) — было
  ``AutoTranslateWidget._format_number``,
  (частично) ``ChapterListWidget._format_char_count``.

Часть 1 — характеризационные тесты самих хелперов (граничные случаи,
по которым копии до этого дословно совпадали или расходились).

Часть 2 — тесты-маршрутизация: подменяют канонический хелпер в
пространстве имён каждого из пяти модулей-вызывающих и проверяют, что
соответствующий метод виджета/страницы действительно идёт через него.
До рефакторинга у каждого вызывающего была своя копия — патч на имя
``format_compact_number``/``format_thousands`` в этих модулях падает
(AttributeError, RED), либо canonical мок остаётся невызванным.
После рефакторинга — вызовы идут через импортированный хелпер (GREEN).
"""
import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from gemini_translator.utils import helpers


class CompactNumberFormatterCharacterizationTests(unittest.TestCase):
    """Поведение канонических хелперов — граничные случаи."""

    def test_format_compact_number_below_thousand_is_plain_str(self):
        self.assertEqual(helpers.format_compact_number(0), "0")
        self.assertEqual(helpers.format_compact_number(999), "999")

    def test_format_compact_number_thousand_threshold_uses_k_suffix(self):
        self.assertEqual(helpers.format_compact_number(1000), "1.0K")
        self.assertEqual(helpers.format_compact_number(1234), "1.2K")

    def test_format_compact_number_just_below_million_stays_k(self):
        # Копии не округляли вверх до "1.0M" — сохраняем этот квирк алгоритма.
        self.assertEqual(helpers.format_compact_number(999_999), "1000.0K")

    def test_format_compact_number_million_threshold_uses_m_suffix(self):
        self.assertEqual(helpers.format_compact_number(1_000_000), "1.0M")
        self.assertEqual(helpers.format_compact_number(2_500_000), "2.5M")

    def test_format_compact_number_invalid_or_missing_defaults_to_zero(self):
        self.assertEqual(helpers.format_compact_number(None), "0")
        self.assertEqual(helpers.format_compact_number("abc"), "0")

    def test_format_thousands_uses_space_separator(self):
        self.assertEqual(helpers.format_thousands(1234567), "1 234 567")
        self.assertEqual(helpers.format_thousands(999), "999")

    def test_format_thousands_zero_and_negative(self):
        self.assertEqual(helpers.format_thousands(0), "0")
        self.assertEqual(helpers.format_thousands(-5), "-5")

    def test_format_thousands_none_defaults_to_zero_string(self):
        self.assertEqual(helpers.format_thousands(None), "0")


class CompactNumberFormatterRoutingTests(unittest.TestCase):
    """Каждое бывшее место дублирования обязано звать канонический хелпер."""

    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_status_bar_widget_routes_through_canonical_compact_formatter(self):
        import gemini_translator.ui.widgets.status_bar_widget as status_bar_widget

        widget = status_bar_widget.StatusBarWidget(event_bus=None, engine=None)
        self.addCleanup(widget.close)
        widget.total_tokens_used = 1500
        widget.input_tokens_used = 1000
        widget.output_tokens_used = 500

        with mock.patch.object(
            status_bar_widget, "format_compact_number", return_value="<compact>"
        ) as fake:
            suffix = widget._format_token_usage_suffix()

        fake.assert_called()
        self.assertIn("<compact>", suffix)

    def test_chapter_list_widget_routes_through_canonical_thousands_formatter(self):
        import gemini_translator.ui.widgets.chapter_list_widget as chapter_list_widget

        widget = chapter_list_widget.ChapterListWidget()
        self.addCleanup(widget.deleteLater)
        widget.set_show_chapter_char_count(True)
        widget.set_chapter_char_counts({"ch1": 12345})

        with mock.patch.object(
            chapter_list_widget, "format_thousands", return_value="<thousands>"
        ) as fake:
            suffix = widget._char_suffix_for_chapters(["ch1"])

        fake.assert_called()
        self.assertIn("<thousands>", suffix)

    def test_auto_translate_widget_routes_through_canonical_thousands_formatter(self):
        import gemini_translator.ui.widgets.auto_translate_widget as auto_translate_widget

        sm = mock.MagicMock()
        sm.get_last_auto_translation_preset_name.return_value = ""
        sm.get_last_auto_translation_settings.return_value = {}
        widget = auto_translate_widget.AutoTranslateWidget(settings_manager=sm)
        self.addCleanup(widget.deleteLater)
        widget.batch_tokens_spin.setValue(1500)

        with mock.patch.object(
            auto_translate_widget, "format_thousands", return_value="<thousands>"
        ) as fake:
            widget._update_translation_profile_summary()

        fake.assert_called()

    def test_consistency_validator_page_routes_through_canonical_compact_formatter(self):
        import gemini_translator.ui.dialogs.consistency_checker as consistency_checker

        with mock.patch.object(
            consistency_checker.ConsistencyValidatorPage, "__init__", lambda self, *a, **k: None
        ):
            page = consistency_checker.ConsistencyValidatorPage()
        page._token_total = 1500
        page._token_input_total = 1000
        page._token_output_total = 500
        page.token_usage_label = mock.MagicMock()

        # pcluster-03: _update_token_usage_label теперь живёт в
        # TokenUsageTrackerMixin (gemini_translator/utils/helpers.py), а не
        # в consistency_checker.py — патчим формировщик там, где его
        # реально вызывает миксин.
        with mock.patch.object(
            helpers, "format_compact_number", return_value="<compact>"
        ) as fake:
            page._update_token_usage_label()

        fake.assert_called()
        page.token_usage_label.setText.assert_called_with("Токены: ~<compact>")

    def test_ai_translation_page_routes_through_canonical_compact_formatter(self):
        # _update_token_usage_label живёт на AITranslationPage (не на
        # UntranslatedFixerPage) — конструктор требует глобальные
        # app.event_bus/app.engine, поэтому __init__ обходим так же, как
        # в test_consistency_checker_theme.py для ConsistencyValidatorPage.
        import gemini_translator.ui.dialogs.validation_dialogs.untranslated_fixer_dialog as untranslated_fixer_dialog

        with mock.patch.object(
            untranslated_fixer_dialog.AITranslationPage, "__init__", lambda self, *a, **k: None
        ):
            page = untranslated_fixer_dialog.AITranslationPage()
        page._token_total = 1500
        page._token_input_total = 1000
        page._token_output_total = 500
        page.token_usage_label = mock.MagicMock()

        # pcluster-03: то же самое — метод переехал в TokenUsageTrackerMixin.
        with mock.patch.object(
            helpers, "format_compact_number", return_value="<compact>"
        ) as fake:
            page._update_token_usage_label()

        fake.assert_called()
        page.token_usage_label.setText.assert_called_with("Токены: ~<compact>")


if __name__ == "__main__":
    unittest.main()
