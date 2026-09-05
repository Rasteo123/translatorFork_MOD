# -*- coding: utf-8 -*-
"""
Регресс-тест для находки utils-text/bugs/2-jieba-global-reload-shared-con.

ChineseTextProcessor — один объект на процесс (см. ContextManager.chinese_processor),
но его использует несколько параллельных TranslationEngine (основная сессия перевода
и параллельный content-filter redirect-движок, см. setup.py:_maybe_start_parallel_filter_redirect).
Каждая сессия при старте обучает Jieba своим глоссарием (add_custom_words), а при
остановке сбрасывает состояние (reset()). До фикса reset() делал безусловный
importlib.reload(jieba) — глобальную перезагрузку модуля, стирающую обучение ВСЕХ
сессий, а не только завершившейся. В результате более короткая сессия (redirect),
завершаясь раньше, стирала обучение ещё активной основной сессии без единой ошибки
в логе.
"""

import unittest

from gemini_translator.utils.language_tools import JIEBA_AVAILABLE, ChineseTextProcessor


TERM_A = "云天河"  # 云天河 - имя персонажа, по умолчанию Jieba режет на 2 токена
TERM_B = "龙傲天"  # 龙傲天 - другое имя, по умолчанию тоже режется на 2 токена


@unittest.skipUnless(JIEBA_AVAILABLE, "jieba требуется для проверки состояния сегментации")
class JiebaSharedProcessorResetTests(unittest.TestCase):
    def test_reset_from_one_session_does_not_erase_training_of_another(self):
        """
        Имитация двух параллельных TranslationEngine, разделяющих один
        ContextManager.chinese_processor (main.py:1198/1202, setup.py:5298-5303):
        сессия A стартует и обучает Jieba своим термином, затем сессия B
        стартует и обучает своим термином, затем сессия B завершается раньше
        (reset()). Обучение сессии A должно пережить остановку сессии B.
        """
        processor = ChineseTextProcessor()

        try:
            # --- Сессия A: apply_and_start_session (translation_engine.py:707-713) ---
            processor.add_custom_words({TERM_A: {"rus": "A"}})
            self.assertEqual(
                processor.segment_text_split(f"{TERM_A}走进了房间。"),
                [TERM_A, "走进", "了", "房间", "。"],
                "Сессия A должна успешно обучить Jieba своему термину",
            )

            # --- Сессия B: параллельный filter-redirect движок стартует ---
            processor.add_custom_words({TERM_B: {"rus": "B"}})
            self.assertEqual(
                processor.segment_text_split(f"{TERM_B}离开了城市。"),
                [TERM_B, "离开", "了", "城市", "。"],
            )

            # --- Сессия B завершается первой: stop_session (translation_engine.py:1076-1079) ---
            processor.reset()

            # Обучение сессии A должно остаться нетронутым, т.к. сессия A ещё активна.
            self.assertEqual(
                processor.segment_text_split(f"{TERM_A}走进了房间。"),
                [TERM_A, "走进", "了", "房间", "。"],
                "reset() второй (более короткой) сессии не должен стирать обучение "
                "ещё активной первой сессии",
            )

            # --- Сессия A тоже завершается: должна произойти полная очистка ---
            processor.reset()

            self.assertEqual(
                processor.segment_text_split(f"{TERM_A}走进了房间。"),
                ["云", "天河", "走进", "了", "房间", "。"],
                "После остановки ПОСЛЕДНЕЙ активной сессии состояние Jieba должно "
                "полностью сброситься до стандартного словаря",
            )
        finally:
            # На случай падения теста в середине сценария досбрасываем состояние
            # (двойной вызов безопасен: reset() — no-op, если jieba уже не инициализирована),
            # чтобы не задеть другие тесты модуля.
            processor.reset()
            processor.reset()


if __name__ == "__main__":
    unittest.main()
