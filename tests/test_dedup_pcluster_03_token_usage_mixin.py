# -*- coding: utf-8 -*-
"""pcluster-03: учёт токенов сессии продублирован между

``ConsistencyValidatorPage._reset_token_usage``/``_update_token_usage_label``
и ``AITranslationPage._reset_token_usage``/``_update_token_usage_label``
(consistency_checker.py и untranslated_fixer_dialog.py) — AST-совпадение,
единственное отличие — текст подсказки ("текущий сеанс" vs "текущую
AI-сессию"). Обработчики события (``on_token_usage_updated`` /
``_on_token_usage_updated``) НЕ объединяются: consistency_checker подписан
напрямую на Qt-сигнал engine.token_usage_updated, а fixer — на общую
EventBus-шину с обязательной проверкой ``_is_owned_session_event``
(см. память пользователя про eventbus-topic-migration и защиту от
cross-talk между окнами). Слияние обработчиков сняло бы эту защиту.

Канонический хелпер: ``gemini_translator.utils.helpers.TokenUsageTrackerMixin``
(``_reset_token_usage`` + ``_update_token_usage_label``, текст подсказки
настраивается атрибутом класса ``_token_usage_tooltip_scope``).

Часть 1 — характеризационные тесты миксина (сброс счётчиков, формат
подписи/тултипа, настраиваемый scope-текст).
Часть 2 — тесты-маршрутизация: до рефакторинга у ``ConsistencyValidatorPage``
и ``AITranslationPage`` были собственные копии этих двух методов (RED —
``is``-проверка идентичности с методом миксина падает); после рефакторинга
оба класса используют ровно миксин-реализацию (GREEN).

Часть 3 (добавлена по замечанию рецензента, major, issue №1): само ядро
кластера — парсинг payload'а + клампинг + накопление трёх счётчиков — было
объединено только частично: ``_reset_token_usage``/``_update_token_usage_label``
уехали в миксин, а идентичные ``on_token_usage_updated``/
``_on_token_usage_updated`` остались продублированы побайтово (обоснование
про разные источники события относится к ФИЛЬТРУ владения сессией, который
живёт выше по стеку в ``_on_global_event``, а не к телу самого накопления).
Ниже — тесты RED-до/GREEN-после на новый метод
``TokenUsageTrackerMixin._accumulate_token_usage``, к которому оба
обработчика теперь лишь тонко делегируют, плюс тест, что owned-session
фильтр в ``_on_global_event`` при этом не тронут.

Часть 4 (minor, issue №3): порядок баз в объявлении класса —
``TokenUsageTrackerMixin`` должен идти ПЕРЕД ``ShellPage``, иначе миксин
оказывается в хвосте MRO, за всеми sip/Qt-предками.
"""
import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gemini_translator.utils import helpers
from gemini_translator.utils.helpers import TokenUsageTrackerMixin


class _Host(TokenUsageTrackerMixin):
    """Минимальный хост миксина: только атрибуты, которых миксин ожидает."""

    def __init__(self):
        self._token_input_total = 0
        self._token_output_total = 0
        self._token_total = 0
        self.token_usage_label = mock.MagicMock()


class TokenUsageTrackerMixinCharacterizationTests(unittest.TestCase):
    def test_reset_zeroes_counters_and_updates_label(self):
        host = _Host()
        host._token_input_total = 10
        host._token_output_total = 20
        host._token_total = 30

        host._reset_token_usage()

        self.assertEqual(host._token_input_total, 0)
        self.assertEqual(host._token_output_total, 0)
        self.assertEqual(host._token_total, 0)
        host.token_usage_label.setText.assert_called_with("Токены: ~0")

    def test_update_label_uses_canonical_compact_formatter(self):
        host = _Host()
        host._token_total = 1500
        host._token_input_total = 1000
        host._token_output_total = 500

        with mock.patch.object(
            helpers, "format_compact_number", return_value="<compact>"
        ) as fake:
            host._update_token_usage_label()

        fake.assert_called()
        host.token_usage_label.setText.assert_called_with("Токены: ~<compact>")

    def test_default_tooltip_scope_is_session(self):
        host = _Host()
        host._token_total = 1500
        host._token_input_total = 1000
        host._token_output_total = 500

        host._update_token_usage_label()

        tooltip = host.token_usage_label.setToolTip.call_args[0][0]
        self.assertIn("текущий сеанс", tooltip)
        self.assertIn("~1.5K", tooltip)

    def test_tooltip_scope_is_overridable_per_subclass(self):
        class _AiSessionHost(_Host):
            _token_usage_tooltip_scope = "текущую AI-сессию"

        host = _AiSessionHost()
        host._token_total = 1500

        host._update_token_usage_label()

        tooltip = host.token_usage_label.setToolTip.call_args[0][0]
        self.assertIn("текущую AI-сессию", tooltip)
        self.assertNotIn("текущий сеанс", tooltip)


class TokenUsageTrackerMixinRoutingTests(unittest.TestCase):
    """Оба бывших места дублирования обязаны использовать ровно миксин."""

    def test_consistency_validator_page_uses_mixin_methods(self):
        import gemini_translator.ui.dialogs.consistency_checker as consistency_checker

        self.assertIs(
            consistency_checker.ConsistencyValidatorPage._reset_token_usage,
            TokenUsageTrackerMixin._reset_token_usage,
        )
        self.assertIs(
            consistency_checker.ConsistencyValidatorPage._update_token_usage_label,
            TokenUsageTrackerMixin._update_token_usage_label,
        )

    def test_ai_translation_page_uses_mixin_methods(self):
        import gemini_translator.ui.dialogs.validation_dialogs.untranslated_fixer_dialog as untranslated_fixer_dialog

        self.assertIs(
            untranslated_fixer_dialog.AITranslationPage._reset_token_usage,
            TokenUsageTrackerMixin._reset_token_usage,
        )
        self.assertIs(
            untranslated_fixer_dialog.AITranslationPage._update_token_usage_label,
            TokenUsageTrackerMixin._update_token_usage_label,
        )

    def test_ai_translation_page_keeps_its_own_ai_session_tooltip_scope(self):
        import gemini_translator.ui.dialogs.validation_dialogs.untranslated_fixer_dialog as untranslated_fixer_dialog

        self.assertEqual(
            untranslated_fixer_dialog.AITranslationPage._token_usage_tooltip_scope,
            "текущую AI-сессию",
        )

    def test_event_handler_names_stay_distinct_per_class(self):
        """Имена/сигнатуры обработчиков события остаются разными по классам:

        ``on_token_usage_updated`` (consistency_checker, pyqtSlot на прямой
        Qt-сигнал engine) и ``_on_token_usage_updated`` (fixer, вызывается
        из ``_on_global_event`` после owned-session фильтра) — это разная
        архитектура ПОДПИСКИ, её объединять не нужно. А вот тело накопления
        внутри них обязано быть общим — см. TokenUsageAccumulationRoutingTests
        ниже.
        """
        import gemini_translator.ui.dialogs.consistency_checker as consistency_checker
        import gemini_translator.ui.dialogs.validation_dialogs.untranslated_fixer_dialog as untranslated_fixer_dialog

        self.assertTrue(hasattr(consistency_checker.ConsistencyValidatorPage, "on_token_usage_updated"))
        self.assertTrue(hasattr(untranslated_fixer_dialog.AITranslationPage, "_on_token_usage_updated"))
        self.assertFalse(hasattr(TokenUsageTrackerMixin, "on_token_usage_updated"))
        self.assertFalse(hasattr(TokenUsageTrackerMixin, "_on_token_usage_updated"))


class TokenUsageAccumulationRoutingTests(unittest.TestCase):
    """issue №1 (major): ядро «учёта токенов» — парсинг+клампинг+накопление —

    должно быть одним каноническим методом (``_accumulate_token_usage``),
    а не продублировано в телах ``on_token_usage_updated`` /
    ``_on_token_usage_updated``.
    """

    def test_mixin_exposes_accumulate_token_usage(self):
        self.assertTrue(
            hasattr(TokenUsageTrackerMixin, "_accumulate_token_usage"),
            "TokenUsageTrackerMixin должен объявлять _accumulate_token_usage "
            "— общее ядро накопления токенов (issue №1 ревью pcluster-03).",
        )

    def test_accumulate_token_usage_body_matches_previous_behaviour(self):
        host = _Host()
        host._token_input_total = 1
        host._token_output_total = 2
        host._token_total = 3

        host._accumulate_token_usage({"input_tokens": 5, "output_tokens": "7"})

        self.assertEqual(host._token_input_total, 6)
        self.assertEqual(host._token_output_total, 9)
        # total_tokens отсутствует в payload -> дефолт input+output (=12), +3 = 15
        self.assertEqual(host._token_total, 15)
        host.token_usage_label.setText.assert_called_with("Токены: ~15")

    def test_accumulate_token_usage_clamps_negatives_and_swallows_bad_types(self):
        host = _Host()
        host._accumulate_token_usage({"input_tokens": -5, "output_tokens": -1, "total_tokens": -9})
        self.assertEqual((host._token_input_total, host._token_output_total, host._token_total), (0, 0, 0))

        host._token_input_total = 42
        host._accumulate_token_usage({"input_tokens": object()})
        # TypeError проглатывается, счётчик не трогается
        self.assertEqual(host._token_input_total, 42)

    def test_consistency_validator_page_slot_delegates_to_accumulator(self):
        import gemini_translator.ui.dialogs.consistency_checker as consistency_checker

        with mock.patch.object(
            consistency_checker.ConsistencyValidatorPage,
            "__init__",
            lambda self, chapters, settings: None,
        ):
            page = consistency_checker.ConsistencyValidatorPage([], None)

        payload = {"input_tokens": 5, "output_tokens": 7}
        page._accumulate_token_usage = mock.MagicMock()

        page.on_token_usage_updated(payload)

        page._accumulate_token_usage.assert_called_once_with(payload)

    def test_ai_translation_page_dispatch_delegates_to_accumulator_when_owned(self):
        from gemini_translator.ui.dialogs.validation_dialogs.untranslated_fixer_dialog import (
            AITranslationPage,
        )

        class _FixerHarness:
            _on_global_event = AITranslationPage._on_global_event
            _is_owned_session_event = AITranslationPage._is_owned_session_event
            _on_token_usage_updated = AITranslationPage._on_token_usage_updated

            def __init__(self):
                self._owned_session_id = "session-1"
                self._accumulate_token_usage = mock.MagicMock()

        harness = _FixerHarness()
        payload = {"input_tokens": 3, "output_tokens": 4}
        harness._on_global_event(
            {"event": "token_usage_updated", "session_id": "session-1", "data": payload}
        )

        harness._accumulate_token_usage.assert_called_once_with(payload)

    def test_ai_translation_page_dispatch_preserves_owned_session_filter(self):
        """Фильтр владения сессией (_is_owned_session_event) в

        ``_on_global_event`` не должен быть затронут переносом ядра
        накопления в миксин: событие от чужой сессии по-прежнему не должно
        доходить до аккумулятора.
        """
        from gemini_translator.ui.dialogs.validation_dialogs.untranslated_fixer_dialog import (
            AITranslationPage,
        )

        class _FixerHarness:
            _on_global_event = AITranslationPage._on_global_event
            _is_owned_session_event = AITranslationPage._is_owned_session_event
            _on_token_usage_updated = AITranslationPage._on_token_usage_updated

            def __init__(self):
                self._owned_session_id = "session-1"
                self._accumulate_token_usage = mock.MagicMock()

        harness = _FixerHarness()
        harness._on_global_event(
            {
                "event": "token_usage_updated",
                "session_id": "some-other-session",
                "data": {"input_tokens": 3, "output_tokens": 4},
            }
        )

        harness._accumulate_token_usage.assert_not_called()


class TokenUsageTrackerMixinMroOrderTests(unittest.TestCase):
    """issue №3 (minor): миксин должен идти ПЕРЕД ShellPage в объявлении

    базовых классов, иначе он в хвосте MRO за всеми sip/Qt-предками, и любое
    случайное совпадение имени выше по цепочке тихо его перекроет.
    """

    def test_consistency_validator_page_mro_prefers_mixin(self):
        import gemini_translator.ui.dialogs.consistency_checker as consistency_checker

        mro = consistency_checker.ConsistencyValidatorPage.__mro__
        self.assertLess(
            mro.index(TokenUsageTrackerMixin),
            mro.index(consistency_checker.ShellPage),
        )

    def test_ai_translation_page_mro_prefers_mixin(self):
        import gemini_translator.ui.dialogs.validation_dialogs.untranslated_fixer_dialog as untranslated_fixer_dialog

        mro = untranslated_fixer_dialog.AITranslationPage.__mro__
        self.assertLess(
            mro.index(TokenUsageTrackerMixin),
            mro.index(untranslated_fixer_dialog.ShellPage),
        )


if __name__ == "__main__":
    unittest.main()
