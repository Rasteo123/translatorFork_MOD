# -*- coding: utf-8 -*-
"""
Регресс-тест на minor-замечание рецензента (line 1994) к находке
utils-text/bugs/2-jieba-global-reload-shared-con (группа g29).

jieba — модуль с процесс-глобальным состоянием (jieba.dt.FREQ), а не
состояние одного объекта ChineseTextProcessor. GlossaryReplacer.cleanup()
(language_tools.py) вызывает reset() на СВОЁМ отдельном ChineseTextProcessor
(SmartGlossaryFilter.__init__ создаёт его отдельно от общего
ContextManager.chinese_processor). Если этот отдельный процессор сам ничего
не обучал (has_cjk_terms=False в prepare()), у него не было бы обучений,
но при чисто ПОЭКЗЕМПЛЯРНОМ счётчике активных обучений reset() всё равно
принял бы "мой счётчик 0" за "все обучения во всём процессе закончились" и
безусловно перезагрузил бы jieba — стерев обучение АКТИВНОЙ сессии перевода,
разделяющей тот же модуль jieba через другой экземпляр процессора.
"""

import unittest

from gemini_translator.utils.language_tools import JIEBA_AVAILABLE, ChineseTextProcessor


TERM = "云天河"  # по умолчанию режется на ['云', '天河']


@unittest.skipUnless(JIEBA_AVAILABLE, "jieba требуется для проверки состояния сегментации")
class JiebaResetIsScopedAcrossInstancesTests(unittest.TestCase):
    def test_reset_on_untrained_instance_does_not_wipe_another_instances_training(self):
        """
        session_processor имитирует общий ContextManager.chinese_processor
        активной сессии перевода. glossary_replacer_processor имитирует
        отдельный ChineseTextProcessor, который создаёт себе
        SmartGlossaryFilter/GlossaryReplacer и который САМ ничего не обучал.
        """
        session_processor = ChineseTextProcessor()
        glossary_replacer_processor = ChineseTextProcessor()

        try:
            # Активная сессия перевода обучает СВОЙ (общий, session_processor) процессор.
            session_processor.add_custom_words({TERM: {"rus": "A"}})
            self.assertEqual(
                session_processor.segment_text_split(f"{TERM}走进了房间。"),
                [TERM, "走进", "了", "房间", "。"],
            )

            # GlossaryReplacer.cleanup() вызывает reset() на СВОЁМ процессоре,
            # который сам ничего не обучал (аналог has_cjk_terms=False в prepare()).
            glossary_replacer_processor.reset()

            # Обучение активной сессии не должно было пострадать: reset()
            # чужого, ничего не обучавшего процессора не имеет права
            # перезагружать общий для всего процесса модуль jieba.
            self.assertEqual(
                session_processor.segment_text_split(f"{TERM}走进了房间。"),
                [TERM, "走进", "了", "房间", "。"],
                "reset() процессора, который сам не обучал jieba (например, "
                "отдельный процессор GlossaryReplacer.cleanup() при "
                "has_cjk_terms=False), не должен стирать обучение ДРУГОГО "
                "процессора, разделяющего тот же общий модуль jieba",
            )

            # А собственный reset() сессии по-прежнему обязан нормально
            # откатывать её обучение до конца.
            session_processor.reset()
            self.assertEqual(
                session_processor.segment_text_split(f"{TERM}走进了房间。"),
                ["云", "天河", "走进", "了", "房间", "。"],
            )
        finally:
            for proc in (session_processor, glossary_replacer_processor):
                for _ in range(3):
                    proc.reset()


if __name__ == "__main__":
    unittest.main()
