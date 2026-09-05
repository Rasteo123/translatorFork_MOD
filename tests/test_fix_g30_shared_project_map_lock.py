"""Регресс на находку utils-io/bugs/5-projectmanager-lock-not-shared.

PatientLock создавался НА ЭКЗЕМПЛЯР TranslationProjectManager, а не на файл
карты проекта. Два живых экземпляра на одну и ту же project_folder (типичный
случай: основное окно перевода + диалог глоссария/анализатора частотности,
открытые параллельно) синхронизировались друг с другом только в момент
КОНСТРУИРОВАНИЯ нового объекта (_flush_pending_for_map), но не между уже
живущими объектами — их flush()/register_multiple_translations и т.п. гоняли
read-modify-write мимо друг друга, и более поздняя запись затирала диск
устаревшим снимком, молча теряя регистрации соседнего экземпляра.
"""

import json
import os
import tempfile
import threading
import unittest

from gemini_translator.utils.project_manager import TranslationProjectManager


class SharedProjectMapLockTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_two_managers_same_folder_share_lock_object(self):
        """Два менеджера на одну и ту же папку проекта обязаны использовать
        ОДИН объект блокировки — иначе их запись карты не сериализована."""
        mgr_a = TranslationProjectManager(self._tmp.name)
        mgr_b = TranslationProjectManager(self._tmp.name)
        self.addCleanup(mgr_a.flush)
        self.addCleanup(mgr_b.flush)
        self.assertIs(mgr_a.lock, mgr_b.lock)

    def test_different_folders_get_independent_locks(self):
        """Менеджеры разных проектов не должны делить один лок (не должны
        сериализоваться друг с другом без необходимости)."""
        with tempfile.TemporaryDirectory() as other_tmp:
            mgr_a = TranslationProjectManager(self._tmp.name)
            mgr_b = TranslationProjectManager(other_tmp)
            self.addCleanup(mgr_a.flush)
            mgr_b.flush()
            self.assertIsNot(mgr_a.lock, mgr_b.lock)

    def test_concurrent_flush_of_two_instances_does_not_lose_registration(self):
        """Боевой сценарий: два экземпляра на одну папку регистрируют разные
        главы и сбрасывают их на диск почти одновременно. Без общего лока
        более поздняя запись затирает диск снимком, снятым ДО того, как
        другой экземпляр дописал свою главу — регистрация теряется молча."""
        mgr_a = TranslationProjectManager(self._tmp.name)
        mgr_b = TranslationProjectManager(self._tmp.name)
        self.addCleanup(mgr_a.flush)
        self.addCleanup(mgr_b.flush)

        mgr_a.register_translation("Text/ch_A.xhtml", "_translated.html", "out/ch_A.html")
        mgr_b.register_translation("Text/ch_B.xhtml", "_translated.html", "out/ch_B.html")

        read_done = threading.Event()
        proceed = threading.Event()
        orig_load_unsafe = TranslationProjectManager._load_unsafe

        def patched_load_unsafe(self):
            data = orig_load_unsafe(self)
            if self is mgr_a:
                # A успел прочитать диск (под своим локом) — даём B шанс
                # вклиниться со своим read-modify-write, пока A ещё «думает».
                read_done.set()
                proceed.wait(0.3)
            return data

        TranslationProjectManager._load_unsafe = patched_load_unsafe
        try:
            thread_a = threading.Thread(target=mgr_a.flush)
            thread_a.start()
            self.assertTrue(read_done.wait(2), "A не дошёл до чтения карты за 2с")
            mgr_b.flush()
            proceed.set()
            thread_a.join(5)
            self.assertFalse(thread_a.is_alive(), "поток A завис")
        finally:
            TranslationProjectManager._load_unsafe = orig_load_unsafe

        map_path = os.path.join(self._tmp.name, "translation_map.json")
        with open(map_path, "r", encoding="utf-8") as f:
            on_disk = json.load(f)

        self.assertIn("Text/ch_A.xhtml", on_disk, "регистрация A потеряна")
        self.assertIn("Text/ch_B.xhtml", on_disk, "регистрация B потеряна из-за несинхронизированной записи")


if __name__ == "__main__":
    unittest.main()
