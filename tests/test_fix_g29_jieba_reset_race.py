# -*- coding: utf-8 -*-
"""
Регресс-тест на minor-замечание рецензента (line 317) к находке
utils-text/bugs/2-jieba-global-reload-shared-con (группа g29).

Решение "перезагружать ли jieba" принималось под локом, а сам
importlib.reload(jieba) выполнялся уже ВНЕ лока (как и обучение в
add_custom_words). Из-за этого возможна гонка: сессия А решает перезагрузить
jieba (счётчик дошёл до нуля) и начинает reload, а в это же время сессия Б
успевает стартовать и обучить jieba своим термином — reload сессии А стирает
свежее обучение Б, хотя формально Б уже считается активной.

Тест форсирует это окно, подменяя importlib.reload на "медленную" версию:
поток reset() входит в reload и сигналит об этом, поток add_custom_words()
ждёт этого сигнала и сразу же пытается обучить jieba. Если критическая
секция обучения и вся тела reset() (включая reload) не сериализованы одним
локом — обучение произойдёт ДО того, как reload физически перезагрузит
модуль, и будет им стёрто.
"""

import importlib
import threading
import time
import unittest
from unittest.mock import patch

from gemini_translator.utils import language_tools as lt
from gemini_translator.utils.language_tools import JIEBA_AVAILABLE, ChineseTextProcessor


TERM_A = "云天河"
TERM_B = "龙傲天"  # по умолчанию режется на ['龙傲', '天']


@unittest.skipUnless(JIEBA_AVAILABLE, "jieba требуется для проверки состояния сегментации")
class JiebaResetReloadIsSerializedWithTrainingTests(unittest.TestCase):
    def test_training_during_reload_is_not_wiped_by_it(self):
        processor = ChineseTextProcessor()
        real_reload = importlib.reload
        reload_started = threading.Event()
        release_reload = threading.Event()

        def slow_reload(module):
            reload_started.set()
            release_reload.wait(timeout=2)
            return real_reload(module)

        try:
            # --- Сессия А обучает и сразу же начинает reset() (единственное ---
            # --- активное обучение -> reset() решает делать полный reload). ---
            processor.add_custom_words({TERM_A: {"rus": "A"}})

            def do_reset():
                processor.reset()

            def do_train():
                # Пытаемся обучить РОВНО в момент, когда reset() уже вошёл
                # в "перезагрузку" jieba — воспроизводит окно гонки из
                # замечания рецензента.
                self.assertTrue(reload_started.wait(timeout=2), "reload не начался вовремя")
                processor.add_custom_words({TERM_B: {"rus": "B"}})

            with patch.object(lt.importlib, "reload", side_effect=slow_reload):
                t_reset = threading.Thread(target=do_reset)
                t_train = threading.Thread(target=do_train)
                t_reset.start()
                t_train.start()
                self.assertTrue(reload_started.wait(timeout=2), "reload не начался вовремя")
                # Даём потоку обучения шанс "прорваться" в критическую секцию,
                # пока reload ещё не завершён — если лок не держится на всём
                # теле reset(), гонка успеет случиться именно здесь.
                time.sleep(0.15)
                release_reload.set()
                t_reset.join(5)
                t_train.join(5)

            # Обучение сессии Б обязано пережить reload сессии А: add_custom_words()
            # должен был либо полностью завершиться ДО начала reload, либо
            # дождаться (заблокироваться на том же локе) его окончания — но
            # не выполниться "посередине" и быть стёртым.
            self.assertEqual(
                processor.segment_text_split(f"{TERM_B}离开了城市。"),
                [TERM_B, "离开", "了", "城市", "。"],
                "add_custom_words(), выполненный во время importlib.reload() "
                "чужого reset(), не должен теряться — обучение и reload "
                "должны быть сериализованы одним локом на всё тело метода",
            )
        finally:
            for _ in range(3):
                processor.reset()


if __name__ == "__main__":
    unittest.main()
