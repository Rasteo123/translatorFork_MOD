"""Дебаунс записи translation_map.json.

Раньше register_translation на КАЖДУЮ главу читал и полностью переписывал
растущий файл карты (суммарная запись за сессию квадратична). Правила:
- регистрация видна в этом же экземпляре немедленно (воркеры и prompt_builder
  делят один экземпляр), но на диск уходит отложенно (debounce) или по flush();
- flush сливает pending с актуальным содержимым файла (другое окно могло
  писать) и пишет атомарно (os.replace, без .tmp-хвостов);
- любой другой писатель карты (unregister/cleanup/...) через _load_unsafe
  видит «диск + pending» и своим сохранением персистит и то и другое;
- таймер дебаунса реально сбрасывает на диск без явного flush.
"""

import json
import os
import tempfile
import time
import unittest

from gemini_translator.utils.project_manager import TranslationProjectManager


class ProjectMapDebounceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.pm = TranslationProjectManager(self._tmp.name)
        self.addCleanup(self.pm.flush)

    def _read_map_file(self):
        if not os.path.exists(self.pm.map_file_path):
            return None
        with open(self.pm.map_file_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def test_registration_visible_in_memory_but_deferred_on_disk(self):
        self.pm.register_translation("Text/ch1.xhtml", "_translated.html", "Text/ch1_tr.html")
        self.assertEqual(
            self.pm.get_versions_for_original("Text/ch1.xhtml"),
            {"_translated.html": "Text/ch1_tr.html"},
        )
        on_disk = self._read_map_file()
        self.assertTrue(on_disk is None or "Text/ch1.xhtml" not in on_disk)

    def test_flush_persists_pending_atomically(self):
        self.pm.register_translation("Text/ch1.xhtml", "_translated.html", "Text/ch1_tr.html")
        self.pm.flush()
        on_disk = self._read_map_file()
        self.assertEqual(on_disk["Text/ch1.xhtml"]["_translated.html"], "Text/ch1_tr.html")
        leftovers = [n for n in os.listdir(self._tmp.name) if ".tmp" in n]
        self.assertEqual(leftovers, [])

    def test_flush_merges_with_external_writer(self):
        self.pm.register_translation("Text/ch1.xhtml", "_translated.html", "Text/ch1_tr.html")
        # Другое окно записало свою главу, пока наша регистрация была отложена
        with open(self.pm.map_file_path, 'w', encoding='utf-8') as f:
            json.dump({"Text/other.xhtml": {"_translated.html": "Text/other_tr.html"}}, f)
        self.pm.flush()
        on_disk = self._read_map_file()
        self.assertIn("Text/ch1.xhtml", on_disk)
        self.assertIn("Text/other.xhtml", on_disk)

    def test_other_writers_persist_pending_too(self):
        self.pm.register_translation("Text/ch1.xhtml", "_translated.html", "Text/ch1_tr.html")
        self.pm.register_translation("Text/ch2.xhtml", "_translated.html", "Text/ch2_tr.html")
        self.pm.remove_translation("Text/ch1.xhtml", "_translated.html")
        on_disk = self._read_map_file()
        self.assertIn("Text/ch2.xhtml", on_disk)      # pending персистнут
        self.assertNotIn("Text/ch1.xhtml", on_disk)   # и удаление применилось

    def test_new_instance_sees_pending_of_existing_one(self):
        """CLI-паттерн: регистрация → новый PM для сборки EPUB. Конструктор
        нового экземпляра сливает pending живых менеджеров той же карты."""
        self.pm.register_translation("Text/ch1.xhtml", "_translated.html", "Text/ch1_tr.html")
        fresh = TranslationProjectManager(self._tmp.name)
        self.addCleanup(fresh.flush)
        self.assertEqual(
            fresh.get_versions_for_original("Text/ch1.xhtml"),
            {"_translated.html": "Text/ch1_tr.html"},
        )

    def test_debounce_timer_flushes_without_explicit_flush(self):
        self.pm.FLUSH_DEBOUNCE_SECONDS = 0.05
        self.pm.register_translation("Text/ch9.xhtml", "_translated.html", "Text/ch9_tr.html")
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            on_disk = self._read_map_file()
            if on_disk and "Text/ch9.xhtml" in on_disk:
                break
            time.sleep(0.02)
        self.assertIn("Text/ch9.xhtml", self._read_map_file() or {})


if __name__ == "__main__":
    unittest.main()
