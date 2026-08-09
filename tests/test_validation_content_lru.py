"""Ограничение памяти окна валидации: LRU вместо вечного удержания HTML.

Правила, которые закрепляются:
- строки results_data после add_result НЕ держат original/translated/validated
  HTML (две полные копии книги на большой скан — источник пикового 1.2ГБ);
- _ensure_row_*_loaded читают с диска через ограниченный ContentLru и НЕ
  пишут контент обратно в result_data;
- явные правки пользователя (is_edited, редактор/AI-починка пишут
  translated_html прямо в result_data) закреплены: загрузчик возвращает их,
  вытеснение их не касается, а рескан строки переносит буфер правки вперёд.
"""

import os
import tempfile
import types
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Порядок импорта важен (циркулярный импорт validation_dialogs)
from gemini_translator.ui.dialogs import validation as validation_module
from gemini_translator.ui.dialogs.validation_dialogs.content_lru import ContentLru


class ContentLruTests(unittest.TestCase):
    def test_dict_like_interface_and_eviction(self):
        lru = ContentLru(max_entries=2)
        lru["a"] = "1"
        lru["b"] = "2"
        self.assertIn("a", lru)
        self.assertEqual(lru["a"], "1")   # обращение освежает "a"
        lru["c"] = "3"                     # вытесняется "b" (старейший)
        self.assertNotIn("b", lru)
        self.assertIn("a", lru)
        self.assertIn("c", lru)
        lru.clear()
        self.assertNotIn("a", lru)


def _make_harness(tmpdir):
    """Плоский объект с привязанными методами диалога (идиома _FixerHarness)."""
    harness = types.SimpleNamespace()
    harness.results_data = {}
    harness.translated_content_cache = ContentLru(max_entries=2)
    harness.original_content_cache = ContentLru(max_entries=2)
    harness.validated_content_cache = ContentLru(max_entries=2)
    harness.original_epub_path = None
    harness.project_manager = None
    harness.translated_folder = tmpdir
    for name in (
        "_ensure_row_translated_html_loaded",
        "_ensure_row_original_html_loaded",
        "_ensure_row_validated_content_loaded",
    ):
        setattr(
            harness, name,
            types.MethodType(
                getattr(validation_module.TranslationValidatorDialog, name), harness
            ),
        )
    return harness


class EnsureLoaderTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.harness = _make_harness(self._tmp.name)

    def _add_row(self, row, name, content):
        path = os.path.join(self._tmp.name, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        self.harness.results_data[row] = {
            "path": path,
            "internal_html_path": f"Text/{name}",
            "translated_html": "",
        }
        return path

    def test_loader_reads_disk_without_storing_in_result_data(self):
        self._add_row(0, "ch0.html", "<p>контент ноль</p>")
        self.assertEqual(
            self.harness._ensure_row_translated_html_loaded(0), "<p>контент ноль</p>"
        )
        self.assertFalse(self.harness.results_data[0].get("translated_html"))

    def test_repeat_load_is_served_from_lru(self):
        path = self._add_row(0, "ch0.html", "<p>из кэша</p>")
        self.harness._ensure_row_translated_html_loaded(0)
        os.remove(path)  # диска больше нет — сработать может только LRU
        self.assertEqual(
            self.harness._ensure_row_translated_html_loaded(0), "<p>из кэша</p>"
        )

    def test_lru_is_bounded_and_evicts_oldest(self):
        paths = [self._add_row(i, f"ch{i}.html", f"<p>глава {i}</p>") for i in range(3)]
        for i in range(3):  # ёмкость 2: после загрузки 0,1,2 строка 0 вытеснена
            self.harness._ensure_row_translated_html_loaded(i)
        os.remove(paths[0])
        self.assertEqual(self.harness._ensure_row_translated_html_loaded(0), "")

    def test_edited_buffer_is_pinned_and_wins_over_disk(self):
        self._add_row(0, "ch0.html", "<p>на диске</p>")
        self.harness.results_data[0]["translated_html"] = "<p>несохранённая правка</p>"
        self.assertEqual(
            self.harness._ensure_row_translated_html_loaded(0),
            "<p>несохранённая правка</p>",
        )


class StripHeavyFieldsTests(unittest.TestCase):
    def test_scan_result_is_stripped(self):
        result = {
            "path": "x", "translated_html": "<p>t</p>",
            "original_html": "<p>o</p>", "validated_content": "<p>v</p>",
            "len_orig": 1,
        }
        validation_module.retain_or_strip_heavy_fields(result, previous_data={})
        self.assertNotIn("translated_html", result)
        self.assertNotIn("original_html", result)
        self.assertNotIn("validated_content", result)
        self.assertEqual(result["len_orig"], 1)

    def test_edited_buffer_is_carried_forward_on_rescan(self):
        result = {"translated_html": "<p>с диска</p>", "original_html": "<p>o</p>"}
        previous = {"is_edited": True, "translated_html": "<p>правка юзера</p>"}
        validation_module.retain_or_strip_heavy_fields(result, previous_data=previous)
        self.assertEqual(result["translated_html"], "<p>правка юзера</p>")
        self.assertNotIn("original_html", result)


if __name__ == "__main__":
    unittest.main()
