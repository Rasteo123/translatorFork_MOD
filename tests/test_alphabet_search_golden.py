"""Golden-тест SmartGlossaryFilter.alphabet_search: пиновка найденных множеств.

Stage-3 нечёткого поиска оптимизируется только при условии «результаты не
меняются вообще». Фикстура — снимок результатов эталонного кода на
детерминированном корпусе (тексты + глоссарий + сетка порогов, покрывающая
все уровни каскада: 100/98/96/94/90/85).

Перегенерация (ТОЛЬКО при осознанном изменении поведения поиска):
    python tests/test_alphabet_search_golden.py --regenerate
"""

import json
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gemini_translator.utils.language_tools import SmartGlossaryFilter

FIXTURE_PATH = os.path.join(
    os.path.dirname(__file__), "fixtures", "alphabet_search_golden.json"
)

THRESHOLDS = (100, 98, 96, 94, 90, 85)

_FILLER = (
    "он она они зал горы ветер дыхание техника поток удар шаг взгляд голос "
    "тишина мгновение сила энергия свет тьма клинок рука сердце разум путь "
    "небо земля река пламя холод искра тень камень трон свиток печать барьер"
).split()

_PLANTED_TERMS = {
    # Точные вхождения
    "Ли Цинь": "Ли Цинь",
    "великий старейшина Мо": "великий старейшина Мо",
    "алая пилюля возрождения": "алая пилюля возрождения",
    # Перестановка слов (уровень ≤98)
    "духовный корень": "корень духовный",
    "меч небесного дракона": "дракона небесного меч",
    # Опечатки (уровни ≤96 / ≤94)
    "секта тысячи звёзд": "секта тысячи звизд",
    "пик разлома небес": "пик разломо небес",
    "древний зверь пустоты": "древний зверь пустаты",
    # Морфология
    "духовная энергия": "духовной энергией",
    # Сильное искажение (ловится только на низких порогах или никогда)
    "печать девяти драконов": "печать двяти драконв",
    "владыка восточного дворца": "владыка васточнава дворца",
}

_DISTRACTOR_TERMS = [
    "лишний персонаж", "великая стена клана", "секрет боевого искусства",
    "мечта о бессмертии", "пила лесоруба", "секира палача", "древо мира",
    "дух реки", "корни горы", "звёздный атлас", "пиковая нагрузка",
    "алый рассвет", "мо жуань", "ли вэй", "цинь шихуан", "дракон запада",
    "пилюля очищения", "разлом пространства", "владыка севера",
    "печать молчания", "энергия хаоса", "зверь рассвета",
]


def _build_cases():
    rng = random.Random(20260809)
    glossary = {term: {"rus": f"перевод {i}"} for i, term in
                enumerate(list(_PLANTED_TERMS) + _DISTRACTOR_TERMS)}

    cases = []
    planted_items = list(_PLANTED_TERMS.items())
    for case_index in range(3):
        rng.shuffle(planted_items)
        words = []
        for term_index, (_, surface_form) in enumerate(planted_items):
            words.extend(rng.choice(_FILLER) for _ in range(rng.randint(15, 40)))
            if (term_index + case_index) % 3 != 2:  # часть терминов не вставляем
                words.append(surface_form)
        words.extend(rng.choice(_FILLER) for _ in range(30))
        cases.append({"case_id": f"chapter_{case_index}", "text": " ".join(words)})
    return glossary, cases


def _run_search(glossary, text, threshold):
    logic = SmartGlossaryFilter()
    normalized = logic._normalize_text(text)
    found = logic.alphabet_search(
        glossary, normalized, fuzzy_threshold=threshold,
        similarity_map=None, pre_found_orig=set(),
    )
    return sorted(found)


def _compute_results():
    glossary, cases = _build_cases()
    results = []
    for case in cases:
        for threshold in THRESHOLDS:
            results.append({
                "case_id": case["case_id"],
                "threshold": threshold,
                "found": _run_search(glossary, case["text"], threshold),
            })
    return results


class AlphabetSearchGoldenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(FIXTURE_PATH, "r", encoding="utf-8") as f:
            cls.golden = json.load(f)

    def test_fixture_is_not_trivial(self):
        """Пустая фикстура пиновала бы ничего: проверяем, что находки есть и
        отличаются между порогами (каскад реально задействован)."""
        found_sets = {json.dumps(entry["found"], ensure_ascii=False)
                      for entry in self.golden}
        self.assertGreater(len(found_sets), 3)
        self.assertTrue(any(entry["found"] for entry in self.golden))

    def test_matches_golden_fixture(self):
        actual = _compute_results()
        self.assertEqual(len(actual), len(self.golden))
        for got, expected in zip(actual, self.golden):
            with self.subTest(case=expected["case_id"], threshold=expected["threshold"]):
                self.assertEqual(got["found"], expected["found"])


if __name__ == "__main__":
    if "--regenerate" in sys.argv:
        os.makedirs(os.path.dirname(FIXTURE_PATH), exist_ok=True)
        with open(FIXTURE_PATH, "w", encoding="utf-8") as f:
            json.dump(_compute_results(), f, ensure_ascii=False, indent=1)
        print(f"Fixture written: {FIXTURE_PATH}")
    else:
        unittest.main()
