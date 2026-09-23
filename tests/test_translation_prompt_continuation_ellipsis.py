"""Промпты перевода не учат открывать абзац многоточием, если тот же персонаж
просто продолжает говорить после законченной фразы.

По Розенталю (§ 4) многоточие в начале текста значит, что продолжается
прерванная речь или прошло время. Модель копирует примеры охотнее правил:
пример монолога с «─ …» в каждом абзаце давал в переводе «— …Меня зовут…»
после законченной реплики того же героя. «─ …» остаётся только для фразы,
оборванной многоточием.
"""

import re
import unittest

from gemini_translator.api import config as api_config

PARAGRAPH_RE = re.compile(r'<p[^>]*>(.*?)</p>', re.S)
TAG_RE = re.compile(r'<[^>]+>')
FINISHED_SENTENCE_RE = re.compile(r'[.!?][»“”"]*$')
BROKEN_OFF_RE = re.compile(r'(?:…|\.\.\.)[»“”"]*$')
CONTINUATION_RE = re.compile(r'^─\s*(?:…|\.\.\.)')
WRONG_MARKERS = ('WRONG', 'Неправильно', 'Плохо')


def _paragraph_pairs(text):
    paragraphs = [TAG_RE.sub('', p).strip() for p in PARAGRAPH_RE.findall(text)]
    return list(zip(paragraphs, paragraphs[1:]))


def _invented_continuations(text):
    """Соседние абзацы, где после законченной фразы следующий открыт «─ …»."""
    return [
        (previous, current)
        for previous, current in _paragraph_pairs(text)
        if FINISHED_SENTENCE_RE.search(previous) and CONTINUATION_RE.match(current)
    ]


def _broken_off_continuations(text):
    return [
        (previous, current)
        for previous, current in _paragraph_pairs(text)
        if BROKEN_OFF_RE.search(previous) and CONTINUATION_RE.match(current)
    ]


def _translation_examples():
    examples = api_config.internal_prompts().get('translation_output_examples', {})
    for language, items in examples.items():
        for item in items:
            if 'Tgt:' in item:
                yield language, item.split('Tgt:', 1)[1]


def _prompt_examples(prompt):
    """Примеры промпта: отдельные строки и блоки из одних строк-абзацев.
    Неправильные примеры (строка с пометкой или блок после «Неправильно:») пропускаются."""
    after_wrong_header = False
    for block in re.split(r'\n\s*\n', prompt or ''):
        lines = block.strip().splitlines()
        if lines and all(line.lstrip().startswith('<p') for line in lines):
            if not after_wrong_header:
                yield block
        else:
            for line in lines:
                if not any(marker in line for marker in WRONG_MARKERS):
                    yield line
        after_wrong_header = block.strip().endswith(':') and any(marker in block for marker in WRONG_MARKERS)


def _translation_prompts():
    prompts = dict(api_config.builtin_translation_prompt_variants())
    prompts['default_prompt.txt'] = api_config.default_prompt()
    prompts['sequential'] = api_config.default_sequential_prompt()
    prompts['manual'] = api_config.default_manual_translation_prompt()
    return prompts


class ContinuationEllipsisExamplesTests(unittest.TestCase):
    def test_examples_do_not_open_a_finished_speakers_next_paragraph_with_ellipsis(self):
        for language, target in _translation_examples():
            with self.subTest(language=language, target=target[:80]):
                self.assertEqual(_invented_continuations(target), [])

    def test_examples_still_continue_a_phrase_broken_off_across_paragraphs(self):
        # Правило сужено, а не удалено: оборванная фраза продолжается с «─ …».
        found = {language for language, target in _translation_examples() if _broken_off_continuations(target)}
        self.assertEqual(found, {'en', 'jp', 'zh', 'ko'})


class ContinuationEllipsisPromptTests(unittest.TestCase):
    def test_prompt_examples_do_not_open_a_finished_speakers_next_paragraph_with_ellipsis(self):
        for name, prompt in _translation_prompts().items():
            for example in _prompt_examples(prompt):
                with self.subTest(prompt=name, example=example[:80]):
                    self.assertEqual(_invented_continuations(example), [])

    def test_wrong_examples_are_left_out_of_the_check(self):
        prompt = (
            "Неправильно:\n\n<p>─ Готово.</p>\n<p>─ …Идём.</p>\n\n"
            "*   WRONG: `<p>─ Готово.</p><p>─ …Идём.</p>`"
        )
        self.assertEqual(list(_prompt_examples(prompt)), [])

    def test_right_block_after_a_rule_is_checked(self):
        prompt = "Правило:\n\n<p>─ Готово.</p>\n<p>─ …Идём.</p>"
        examples = list(_prompt_examples(prompt))
        self.assertEqual(_invented_continuations(examples[-1]), [("─ Готово.", "─ …Идём.")])


if __name__ == '__main__':
    unittest.main()
