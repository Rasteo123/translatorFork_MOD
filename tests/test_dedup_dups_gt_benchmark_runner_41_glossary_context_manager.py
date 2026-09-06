"""dups-gt_benchmark_runner-41 (finding utils-text/design/7-format-glossary-for-prompt-ben).

Бенчмарк-раннер держал собственную упрощённую копию
ContextManager.format_glossary_for_prompt (без версионности/fuzzy-фильтрации/
explanation-блока/перемешивания) и подсовывал её PromptBuilder-у в
'project'-режиме бенчмарка — то есть именно там, где важна параритетность с
реальным пайплайном перевода. Эти тесты фиксируют, что 'project'-режим идёт
через канонический ContextManager.format_glossary_for_prompt, а 'raw'-режим
(намеренно упрощённый предпросмотр, не претендующий на паритет) сохраняет
свой лёгкий читаемый формат.
"""

import unittest
from pathlib import Path
from unittest.mock import patch

from gemini_translator.benchmark.runner import build_prompt_bundle
from gemini_translator.utils.glossary_tools import ContextManager


GLOSSARY = [{"original": "hello", "rus": "привет"}]


def _project_prompt_spec(**overrides):
    spec = {
        "id": "p1",
        "mode": "project",
        "template": "TEXT:{text}\nGLOSSARY:{glossary}",
    }
    spec.update(overrides)
    return spec


def _raw_prompt_spec(**overrides):
    spec = {
        "id": "r1",
        "mode": "raw",
        "template": "TEXT:{text}\nGLOSSARY:{glossary}",
    }
    spec.update(overrides)
    return spec


def _case_spec(**overrides):
    spec = {
        "id": "c1",
        "source_html": "<p>hello world</p>",
        "glossary": GLOSSARY,
    }
    spec.update(overrides)
    return spec


class BuildPromptBundleGlossaryRoutingTests(unittest.TestCase):
    """(б) Маршрутизация: 'project'-режим обязан идти через канонический
    ContextManager.format_glossary_for_prompt, а не через свою копию.

    ДО рефакторинга этот тест падает: build_prompt_bundle строит
    _BenchmarkContextManager с собственной реализацией метода, и
    ContextManager.format_glossary_for_prompt вообще не вызывается.
    """

    def test_project_mode_routes_glossary_formatting_through_canonical_context_manager(self):
        with patch.object(
            ContextManager,
            "format_glossary_for_prompt",
            autospec=True,
            return_value="__CANONICAL_MARKER__",
        ) as mocked:
            bundle = build_prompt_bundle(
                _project_prompt_spec(),
                _case_spec(),
                defaults={},
                base_dir=Path("."),
            )

        self.assertTrue(
            mocked.called,
            "PromptBuilder в 'project'-режиме должен вызывать "
            "ContextManager.format_glossary_for_prompt, а не копию бенчмарка",
        )
        self.assertIn("__CANONICAL_MARKER__", bundle.user_prompt)


class BuildPromptBundleGlossaryFormatCharacterizationTests(unittest.TestCase):
    """(а) Характеризация формата: 'project' получает канонический
    JSON-блок {"s":.., "t":..}, а не мок-формат {"original":.., "rus":..}."""

    def test_project_mode_prompt_contains_canonical_glossary_json_shape(self):
        bundle = build_prompt_bundle(
            _project_prompt_spec(),
            _case_spec(),
            defaults={},
            base_dir=Path("."),
        )

        self.assertIn('"s": "hello"', bundle.user_prompt)
        self.assertIn('"t": "привет"', bundle.user_prompt)
        self.assertNotIn('"original": "hello"', bundle.user_prompt)

    def test_raw_mode_preview_keeps_its_own_simplified_readable_format(self):
        """Режим 'raw' намеренно упрощён и не идёт через полный
        PromptBuilder/ContextManager — паритет с production для него не
        заявлен, поэтому лёгкий формат {"original":.., "rus":..} остаётся."""
        bundle = build_prompt_bundle(
            _raw_prompt_spec(),
            _case_spec(),
            defaults={},
            base_dir=Path("."),
        )

        self.assertIn('"original": "hello"', bundle.user_prompt)
        self.assertIn('"rus": "привет"', bundle.user_prompt)

    def test_raw_mode_preview_filters_entries_absent_from_source_text(self):
        bundle = build_prompt_bundle(
            _raw_prompt_spec(),
            _case_spec(
                source_html="<p>no match here</p>",
                glossary=[{"original": "hello", "rus": "привет"}],
            ),
            defaults={},
            base_dir=Path("."),
        )

        self.assertNotIn("привет", bundle.user_prompt)


class BuildPromptBundleProjectModeFiltersByTextTests(unittest.TestCase):
    """Замечание рецензента (major, dups-gt_benchmark_runner-41): docstring и
    отчёт исправителя обещали 'project'-режиму «детерминированный список
    терминов по точному вхождению», но use_dynamic_glossary=False отключает
    фильтрацию вовсе — в промпт уходит весь глоссарий кейса, включая термины,
    отсутствующие в тексте. Удалённая _BenchmarkContextManager это вхождение
    проверяла. Эти тесты фиксируют обещанное (и утраченное) поведение.

    ДО фикса падают: в промпте оказываются 'dragon'/'zzz' и их переводы,
    хотя их нет в source_html.
    """

    def _wide_glossary_case(self):
        return _case_spec(
            source_html="<p>hello world</p>",
            glossary=[
                {"original": "hello", "rus": "привет"},
                {"original": "dragon", "rus": "дракон"},
                {"original": "zzz", "rus": "ззз"},
            ],
        )

    def test_project_mode_drops_glossary_terms_absent_from_source_text(self):
        bundle = build_prompt_bundle(
            _project_prompt_spec(),
            self._wide_glossary_case(),
            defaults={},
            base_dir=Path("."),
        )

        # Проверяем именно JSON-записи глоссария (а не текст промпта целиком):
        # дефолтный шаблон подмешивает независимые format_examples, где слова
        # "dragon"/"дракон" могут встретиться в примере диалога и дать
        # ложное срабатывание, не имеющее отношения к фильтрации глоссария.
        self.assertIn('"s": "hello"', bundle.user_prompt)
        self.assertNotIn('"s": "dragon"', bundle.user_prompt)
        self.assertNotIn('"t": "дракон"', bundle.user_prompt)
        self.assertNotIn('"s": "zzz"', bundle.user_prompt)
        self.assertNotIn('"t": "ззз"', bundle.user_prompt)

    def test_project_mode_keeps_terms_present_in_source_text(self):
        bundle = build_prompt_bundle(
            _project_prompt_spec(),
            _case_spec(
                source_html="<p>hello dragon world</p>",
                glossary=[
                    {"original": "hello", "rus": "привет"},
                    {"original": "dragon", "rus": "дракон"},
                    {"original": "zzz", "rus": "ззз"},
                ],
            ),
            defaults={},
            base_dir=Path("."),
        )

        self.assertIn('"s": "hello"', bundle.user_prompt)
        self.assertIn('"s": "dragon"', bundle.user_prompt)
        self.assertNotIn('"s": "zzz"', bundle.user_prompt)


class BuildPromptBundleProjectModeReproducibilityTests(unittest.TestCase):
    """Замечание рецензента (minor, dups-gt_benchmark_runner-41): переход на
    настоящий ContextManager унаследовал random.shuffle в
    _reorder_glossary_items, из-за чего 'project'-промпт с несколькими
    терминами глоссария менялся между одинаковыми вызовами
    build_prompt_bundle — это ломает сопоставимость прогонов бенчмарка.

    ДО фикса тест нестабилен (флаки, но статистически почти всегда падает
    на глоссарии из 6 терминов: 1/720 шанс случайно угадать порядок дважды).
    """

    def test_project_mode_prompt_is_reproducible_across_repeated_calls(self):
        glossary = [
            {"original": term, "rus": f"т_{term}"}
            for term in ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot"]
        ]
        case = _case_spec(
            source_html="<p>alpha bravo charlie delta echo foxtrot</p>",
            glossary=glossary,
        )

        first = build_prompt_bundle(_project_prompt_spec(), case, defaults={}, base_dir=Path("."))
        second = build_prompt_bundle(_project_prompt_spec(), case, defaults={}, base_dir=Path("."))

        self.assertEqual(first.user_prompt, second.user_prompt)

    def test_project_mode_does_not_leak_deterministic_seed_into_global_random_state(self):
        """Фиксация seed'а ради воспроизводимости бенчмарка не должна быть
        побочным эффектом для остального процесса: после вызова глобальный
        random обязан вернуться в то состояние, в котором был до вызова —
        иначе любой другой код в этом же процессе (не только бенчмарк)
        внезапно получит детерминированную последовательность."""
        import random

        glossary = [
            {"original": term, "rus": f"т_{term}"}
            for term in ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot"]
        ]
        case = _case_spec(
            source_html="<p>alpha bravo charlie delta echo foxtrot</p>",
            glossary=glossary,
        )

        random.seed(12345)
        state_before = random.getstate()
        build_prompt_bundle(_project_prompt_spec(), case, defaults={}, base_dir=Path("."))
        state_after = random.getstate()

        self.assertEqual(state_before, state_after)


if __name__ == "__main__":
    unittest.main()
