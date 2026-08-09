"""Эквивалентность fuzzy_compat (rapidfuzz) и fuzzywuzzy на корпусе пар.

Прослойка обязана давать БАЙТ-В-БАЙТ те же целочисленные скоры, что fuzzywuzzy
с установленным python-Levenshtein: пороговая логика Stage-3 глоссария и
consistency checker откалибрована под них. Ключевые ловушки, покрытые корпусом:
- fuzzywuzzy token_set_ratio по умолчанию force_ascii=True — вырезает символы
  Latin-1 (128..255, например é), но НЕ кириллицу и НЕ CJK;
- целочисленное округление round() финального скора;
- пустые строки и строки, пустеющие после препроцессинга (пунктуация).
"""

import random
import unittest

from fuzzywuzzy import fuzz as legacy_fuzz

from gemini_translator.utils import fuzzy_compat


def _mutated_pairs(seed=20260809, count=120):
    """Детерминированные псевдослучайные пары: базовое слово + мутации
    (замена/вставка/удаление символа, перестановка слов)."""
    rng = random.Random(seed)
    bases = [
        "ли цинь", "великий старейшина мо", "духовный корень",
        "цзянь ван предок меча", "алая пилюля возрождения",
        "xiao yan", "меч небесного дракона", "секта тысячи звёзд",
        "森罗万象 древний зверь", "пик разлома небес",
    ]
    alphabet = "абвгдежзиклмнопрстуфхцчшщэюяqwertyuiop"
    pairs = []
    for _ in range(count):
        base = rng.choice(bases)
        mutated = list(base)
        for _ in range(rng.randint(0, 3)):
            op = rng.choice(("sub", "ins", "del", "swap_words"))
            if op == "swap_words":
                words = "".join(mutated).split()
                rng.shuffle(words)
                mutated = list(" ".join(words))
            elif op == "sub" and mutated:
                pos = rng.randrange(len(mutated))
                mutated[pos] = rng.choice(alphabet)
            elif op == "ins":
                pos = rng.randrange(len(mutated) + 1)
                mutated.insert(pos, rng.choice(alphabet))
            elif op == "del" and mutated:
                del mutated[rng.randrange(len(mutated))]
        pairs.append((base, "".join(mutated)))
    return pairs


CORPUS = [
    # Идентичные и почти идентичные (пороговая зона)
    ("Ли Цинь", "Ли Цинь"),
    ("Ли Цинь", "Ли Цынь"),
    ("старейшина Мо", "старейшина Моо"),
    # Перестановка и подмножество слов (ядро token_set)
    ("великий старейшина мо", "мо старейшина великий"),
    ("великий старейшина мо", "старейшина мо"),
    ("духовный корень пятого ранга", "корень духовный"),
    # CJK и смешанные
    ("森罗万象", "森罗万象"),
    ("森罗万象", "森罗方象"),
    ("зверь 森罗", "森罗 зверь древний"),
    ("xiǎo yàn", "xiao yan"),
    # Latin-1 квирк force_ascii (é/ü вырезаются fuzzywuzzy)
    ("Ferrét", "Ferret"),
    ("Müller герой", "Muller герой"),
    ("café де мир", "cafe де мир"),
    # Пустое и пустеющее после препроцессинга
    ("", ""),
    ("Ли Цинь", ""),
    ("!!!", "???"),
    ("...", "Ли Цинь"),
    # Цифры, регистр, разделители
    ("Глава 123", "глава 123"),
    ("меч-дракон", "меч дракон"),
    ("a_b_c", "a b c"),
    ("Пилюля Ци 3-го ранга", "пилюля ци 3 ранга"),
    # Совсем разные
    ("Ли Цинь", "восточный дворец"),
    ("секта тысячи звёзд", "faceless swordsman"),
] + _mutated_pairs()


class FuzzyCompatTests(unittest.TestCase):
    def test_backend_is_rapidfuzz(self):
        """Если rapidfuzz не установлен, прослойка тихо падает в fuzzywuzzy —
        и «оптимизация» превращается в no-op. Фиксируем боевой бэкенд."""
        self.assertEqual(fuzzy_compat.FUZZY_BACKEND, "rapidfuzz")
        self.assertTrue(fuzzy_compat.FUZZ_AVAILABLE)

    def test_ratio_matches_fuzzywuzzy_on_corpus(self):
        for s1, s2 in CORPUS:
            with self.subTest(s1=s1, s2=s2):
                self.assertEqual(
                    fuzzy_compat.ratio(s1, s2),
                    legacy_fuzz.ratio(s1, s2),
                )

    def test_token_set_ratio_matches_fuzzywuzzy_on_corpus(self):
        for s1, s2 in CORPUS:
            with self.subTest(s1=s1, s2=s2):
                self.assertEqual(
                    fuzzy_compat.token_set_ratio(s1, s2),
                    legacy_fuzz.token_set_ratio(s1, s2),
                )

    def test_preclean_matches_full_path_on_precleaned_input(self):
        """token_set_ratio_preclean применим только к строкам после
        universal_cleaner (\\W+→' ', lower, strip) — на них он обязан давать
        тот же скор, что полный путь и fuzzywuzzy."""
        import re
        cleaner = re.compile(r"\W+", re.UNICODE)
        for s1, s2 in CORPUS:
            c1 = cleaner.sub(" ", s1.lower()).strip()
            c2 = cleaner.sub(" ", s2.lower()).strip()
            with self.subTest(s1=c1, s2=c2):
                expected = legacy_fuzz.token_set_ratio(c1, c2)
                self.assertEqual(fuzzy_compat.token_set_ratio_preclean(c1, c2), expected)
                self.assertEqual(fuzzy_compat.token_set_ratio(c1, c2), expected)

    def test_returns_int_like_fuzzywuzzy(self):
        self.assertIsInstance(fuzzy_compat.ratio("Ли Цинь", "Ли Цынь"), int)
        self.assertIsInstance(
            fuzzy_compat.token_set_ratio("старейшина мо", "мо великий"), int
        )


if __name__ == "__main__":
    unittest.main()
