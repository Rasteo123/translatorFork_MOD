"""_append_jsonl: mkdir не на каждый вызов, но удалённая папка пересоздаётся.

Пиновка перед оптимизацией: раньше mkdir(parents=True) шёл на каждый append
(syscall на каждое debug-событие); с кэшем созданных папок обязан остаться
рабочим сценарий «пользователь удалил папку логов на лету».
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from gemini_translator.utils.debug_logger import _append_jsonl


class DebugAppendTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _read_lines(self, path):
        with open(path, encoding='utf-8') as f:
            return [json.loads(line) for line in f]

    def test_append_creates_nested_dirs_and_recreates_after_delete(self):
        log_path = Path(self._tmp.name) / "session" / "op" / "events.jsonl"
        _append_jsonl(log_path, {"event": "первый"})
        self.assertEqual(self._read_lines(log_path)[0]["event"], "первый")

        shutil.rmtree(log_path.parent.parent)  # пользователь удалил папку логов
        _append_jsonl(log_path, {"event": "после удаления"})
        self.assertEqual(self._read_lines(log_path)[0]["event"], "после удаления")


if __name__ == "__main__":
    unittest.main()
