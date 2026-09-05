# -*- coding: utf-8 -*-
"""
Регресс-тест на major-замечание рецензента к находке
utils-text/bugs/2-jieba-global-reload-shared-con (группа g29).

Прошлая правка (простой refcount) вводила НОВЫЙ постоянный отказ очистки:
если add_custom_words() вызван без парного reset() (ровно так гасится
параллельный content-filter redirect-движок — engine.cleanup() в обход
stop_session(), см. core/translation_engine.py:1567-1574 — правка вне зоны
этой группы), счётчик активных обучений навсегда застревал > 0, и reset()
переставал работать до конца жизни процесса: обучение ВСЕХ последующих
сессий перевода накапливалось в jieba и никогда не сбрасывалось.

Репро рецензента: обучить процессор термином А (имитация непарно убитого
redirect-движка), затем термином Б (новая обычная сессия) и вызвать reset()
один раз (как это делает нормально завершившаяся сессия Б). До фикса
reset() был бы полным no-op (только декремент счётчика), поэтому термин Б
остался бы обученным. После фикса reset() обязан откатить именно термин Б
(его добавил вызов, парный этому reset()), а «зависшим» может остаться
только непарно обученный термин А — и, что важнее, reset() не должен
выключаться навсегда: следующий полноценный цикл add_custom_words()/reset()
для НОВОГО термина В обязан снова успешно обучить и снова успешно откатить.
"""

import unittest

from gemini_translator.utils.language_tools import JIEBA_AVAILABLE, ChineseTextProcessor


TERM_LEAKED = "云天河"   # непарно обученный термин (redirect-движок, убитый без reset())
TERM_B = "龙傲天"        # термин обычной сессии, которая нормально доходит до reset() (по умолчанию режется на ['龙傲','天'])
TERM_C = "叶良辰"        # термин следующей (уже третьей) сессии (по умолчанию режется на ['叶','良辰']) — доказывает, что reset() не сломан навсегда


@unittest.skipUnless(JIEBA_AVAILABLE, "jieba требуется для проверки состояния сегментации")
class JiebaResetSurvivesUnpairedTrainingTests(unittest.TestCase):
    def test_reset_does_not_get_stuck_forever_after_unpaired_training(self):
        processor = ChineseTextProcessor()
        try:
            # --- "Redirect-движок": обучил и был убит МИМО reset() ---
            processor.add_custom_words({TERM_LEAKED: {"rus": "leaked"}})
            self.assertEqual(
                processor.segment_text_split(f"{TERM_LEAKED}走进了房间。"),
                [TERM_LEAKED, "走进", "了", "房间", "。"],
            )

            # --- Обычная сессия Б: стартует, обучает термином Б ---
            processor.add_custom_words({TERM_B: {"rus": "B"}})
            self.assertEqual(
                processor.segment_text_split(f"{TERM_B}离开了城市。"),
                [TERM_B, "离开", "了", "城市", "。"],
            )

            # --- Сессия Б корректно доходит до конца и вызывает reset() один раз ---
            processor.reset()

            # reset() обязан был реально откатить термин своей собственной
            # (парной) сессии Б — а не молча стать no-op'ом из-за того, что
            # где-то раньше был непарный add_custom_words().
            self.assertEqual(
                processor.segment_text_split(f"{TERM_B}离开了城市。"),
                ["龙傲", "天", "离开", "了", "城市", "。"],
                "reset() парной сессии не должен превращаться в no-op из-за "
                "постороннего непарного обучения (найдено major-замечанием: "
                "счётчик мог застревать > 0 навсегда)",
            )

            # --- Доказываем, что reset() не выключен навсегда: следующий ---
            # --- ПОЛНОЦЕННЫЙ цикл (новая сессия В) снова работает штатно. ---
            processor.add_custom_words({TERM_C: {"rus": "C"}})
            self.assertEqual(
                processor.segment_text_split(f"{TERM_C}回来了。"),
                [TERM_C, "回来", "了", "。"],
                "Новая сессия В должна успешно обучить Jieba, несмотря на "
                "оставшуюся зависшую запись сессии-редиректа",
            )
            processor.reset()
            self.assertEqual(
                processor.segment_text_split(f"{TERM_C}回来了。"),
                ["叶", "良辰", "回来", "了", "。"],
                "reset() сессии В тоже должен реально откатывать её обучение — "
                "очистка jieba не должна быть выключена навсегда одним "
                "непарным add_custom_words() где-то в прошлом",
            )
        finally:
            # Финальная зачистка: снимаем всё, что могло остаться в общем
            # стеке ПОСЛЕ данного теста (включая намеренно "зависший" TERM_LEAKED),
            # чтобы не задеть другие тесты модуля. reset() у нас теперь снимает
            # по одному собственному обучению за вызов — вызываем с запасом.
            for _ in range(5):
                processor.reset()


if __name__ == "__main__":
    unittest.main()
