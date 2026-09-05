"""
Тест на дефект core-b/design/2-provider-orchestrator-naive-to.

_estimate_input_tokens в provider_orchestrator.py считал токены как
max(число "слов", len(text)/4) — единый коэффициент 4 симв/токен для
любого алфавита. Для кириллицы/CJK это занижает реальное число токенов
в ~1.8-2.7 раза (канонический estimate_gemini_tokens из utils/helpers.py
использует 2.2 симв/токен для кириллицы и 1.5 для CJK). Из-за этого
_synthesis_context_budget_exceeded может решить, что синтез-промпт
влезает в контекстное окно целевой модели, хотя реально не влезает —
и вместо штатного отката на best_score/first_success запрос уходит в
модель с маленьким окном (типично для локальных/бесплатных провайдеров).
"""

import unittest

from gemini_translator.core.worker_helpers import provider_orchestrator as orchestrator
from gemini_translator.utils.helpers import estimate_gemini_tokens


class EstimateInputTokensAlphabetAwareTests(unittest.TestCase):
    def test_cyrillic_text_is_not_underestimated_by_flat_chars_per_token(self):
        # Один "кусок" без пробелов (compact_pieces=1), чтобы старая эвристика
        # не подстраховывалась подсчётом слов.
        text = "я" * 3800

        naive_like = (len(text) + 3) // 4  # старое поведение: chars/4, округление вверх
        real = estimate_gemini_tokens(text)

        # Подтверждаем сам факт занижения канонической оценкой (документирует дефект).
        self.assertGreater(real, naive_like)

        estimated = orchestrator._estimate_input_tokens(text)
        # После фикса оценка должна соответствовать alphabet-aware estimate_gemini_tokens,
        # а не наивной chars/4 эвристике.
        self.assertEqual(estimated, real)

    def test_synthesis_budget_check_catches_cyrillic_overflow_missed_by_naive_estimate(self):
        text = "я" * 3800  # компактный, "слов" по \S+ всего одно

        naive_char_estimate = (len(text) + orchestrator.TOKEN_ESTIMATE_CHARS_PER_TOKEN - 1) // (
            orchestrator.TOKEN_ESTIMATE_CHARS_PER_TOKEN
        )
        budget = 1000
        usable_budget = max(1, int(budget * orchestrator.SYNTHESIS_CONTEXT_BUDGET_HEADROOM))

        # Убеждаемся, что сценарий действительно воспроизводит дефект:
        # наивная оценка укладывается в бюджет, а реальная (alphabet-aware) — нет.
        self.assertLessEqual(naive_char_estimate, usable_budget)
        self.assertGreater(estimate_gemini_tokens(text), usable_budget)

        worker = type("W", (), {"model_config": {"id": "tiny", "context_window": budget}})()

        exceeded, message = orchestrator._synthesis_context_budget_exceeded(worker, text)

        self.assertTrue(exceeded, "страж бюджета обязан сработать на кириллическом переполнении")
        self.assertIn("exceeds", message)


if __name__ == "__main__":
    unittest.main()
