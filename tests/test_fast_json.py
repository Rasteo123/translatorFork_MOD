"""fast_json (orjson с фолбэком на stdlib): совместимость с json-вызовами кэшей.

Модуль заменяет json.load/dump ТОЛЬКО в местах с ensure_ascii=False и без
object_hook (кэши валидации/частот/анализа глав, модель книги epub_json).
Критичные свойства, закреплённые здесь:
- кортежи сериализуются как массивы (stdlib json делает так же);
- NaN/Infinity при записи становятся null (поведение orjson, валидный JSON);
  чтение СТАРЫХ файлов со stdlib-литералом NaN обязано работать (фолбэк);
- int за пределами 64 бит orjson отвергает — обязан сработать фолбэк stdlib;
- indent=2 и sort_keys поддержаны; результат парсится в тот же объект.
"""

import io
import json
import math
import unittest

from gemini_translator.utils import fast_json

SAMPLES = [
    {"глава": "Text/глава_1.xhtml", "ratio": 1.0523, "ok": True, "n": None},
    {"вложенность": {"список": [1, 2.5, "три", None, False], "cjk": "森罗万象"}},
    {"кортеж": (1, "два", 3.0), "пустые": [{}, [], ""]},
    [1, 2, 3, {"k": "v"}],
    {"big": 2**53 - 1, "neg": -42, "zero": 0.0},
    {"huge_int_beyond_64bit": 2**70},
    "просто строка с ёлочками «» и \n переводом строки",
]


class FastJsonTests(unittest.TestCase):
    def test_backend_is_orjson(self):
        self.assertEqual(fast_json.FAST_JSON_BACKEND, "orjson")

    def test_roundtrip_matches_stdlib(self):
        for sample in SAMPLES:
            with self.subTest(sample=str(sample)[:50]):
                fast_text = fast_json.dumps(sample)
                std_text = json.dumps(sample, ensure_ascii=False)
                # Парсенные представления обязаны совпадать (кортежи → списки)
                self.assertEqual(json.loads(fast_text), json.loads(std_text))
                self.assertEqual(fast_json.loads(fast_text), json.loads(std_text))

    def test_indent_and_sort_keys_parse_back(self):
        payload = {"b": 1, "a": {"г": "д", "в": [1, 2]}, "я": None}
        text = fast_json.dumps(payload, indent=2, sort_keys=True)
        self.assertEqual(json.loads(text), payload)
        # sort_keys реально применён
        self.assertLess(text.index('"a"'), text.index('"b"'))

    def test_dump_and_load_file_objects(self):
        payload = {"файл": [1, 2, {"x": "ы"}]}
        buffer = io.StringIO()
        fast_json.dump(payload, buffer, indent=2, sort_keys=True)
        buffer.seek(0)
        self.assertEqual(fast_json.load(buffer), payload)
        buffer.seek(0)
        self.assertEqual(json.load(buffer), payload)

    def test_nan_becomes_null_and_legacy_nan_files_still_load(self):
        payload = {"ratio": float("nan"), "ok": 1}
        parsed = fast_json.loads(fast_json.dumps(payload))
        self.assertIsNone(parsed["ratio"])  # orjson: NaN → null (валидный JSON)
        self.assertEqual(parsed["ok"], 1)
        # Старые кэши, записанные stdlib, содержат литерал NaN — читаем фолбэком
        legacy_text = json.dumps(payload, ensure_ascii=False)
        legacy = fast_json.loads(legacy_text)
        self.assertTrue(math.isnan(legacy["ratio"]))


if __name__ == "__main__":
    unittest.main()
