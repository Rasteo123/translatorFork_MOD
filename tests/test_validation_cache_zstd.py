"""zstd-хранение кэша валидации (validation_analysis_cache.json.zst).

Правила:
- save пишет сжатый файл (zstd magic) атомарно и убирает легаси-.json,
  чтобы два файла не разъезжались;
- load читает сжатый; при его отсутствии — легаси-.json (старые проекты);
- битый сжатый файл даёт {} (как сегодня битый json) → пересканирование.
"""

import json
import os
import tempfile
import unittest

from gemini_translator.utils.project_manager import TranslationProjectManager

PAYLOAD = {
    "entries": {f"Text/глава_{i}.xhtml": {"ratio": 1.05, "words": ["森", "тест"] * 5}
                for i in range(40)},
    "version": 7,
}


class ValidationCacheZstdTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.pm = TranslationProjectManager(self._tmp.name)
        self.addCleanup(self.pm.flush)

    def test_save_writes_compressed_and_removes_legacy(self):
        with open(self.pm.validation_cache_path, 'w', encoding='utf-8') as f:
            json.dump({"stale": True}, f)
        self.pm.save_validation_cache(PAYLOAD)

        zst_path = self.pm.validation_cache_zst_path
        self.assertTrue(os.path.exists(zst_path))
        with open(zst_path, 'rb') as f:
            self.assertEqual(f.read(4), b"\x28\xb5\x2f\xfd")
        self.assertFalse(os.path.exists(self.pm.validation_cache_path))
        self.assertEqual(
            [n for n in os.listdir(self._tmp.name) if n.endswith('.tmp')], []
        )

    def test_roundtrip(self):
        self.pm.save_validation_cache(PAYLOAD)
        self.assertEqual(self.pm.load_validation_cache(), PAYLOAD)

    def test_legacy_json_still_loads(self):
        with open(self.pm.validation_cache_path, 'w', encoding='utf-8') as f:
            json.dump(PAYLOAD, f, ensure_ascii=False)
        self.assertEqual(self.pm.load_validation_cache(), PAYLOAD)

    def test_corrupted_compressed_cache_returns_empty(self):
        with open(self.pm.validation_cache_zst_path, 'wb') as f:
            f.write(b"\x28\xb5\x2f\xfd" + "битое".encode("utf-8"))
        self.assertEqual(self.pm.load_validation_cache(), {})


if __name__ == "__main__":
    unittest.main()
