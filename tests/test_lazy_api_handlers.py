"""Ленивая загрузка API-хендлеров: import factory не тянет 79МБ зависимостей.

Правила:
- импорт gemini_translator.api.factory НЕ импортирует тяжёлые опциональные
  зависимости (curl_cffi, playwright, flask) и сами модули хендлеров;
- get_api_handler_class грузит ровно запрошенный модуль по требованию
  (PEP 562 __getattr__ в handlers/__init__) и кэширует атрибут;
- неизвестное имя по-прежнему даёт ValueError с внятным сообщением.
"""

import os
import subprocess
import sys
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(code: str) -> str:
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, cwd=_REPO_ROOT
    )
    if out.returncode != 0:
        raise AssertionError(out.stderr[-2000:])
    return out.stdout


class LazyApiHandlersTests(unittest.TestCase):
    def test_factory_import_does_not_load_heavy_deps(self):
        stdout = _run(
            "import sys\n"
            "import gemini_translator.api.factory\n"
            "heavy = [m for m in ('curl_cffi', 'playwright', 'flask',\n"
            "                     'gemini_translator.api.handlers.browser',\n"
            "                     'gemini_translator.api.handlers.local')\n"
            "         if m in sys.modules]\n"
            "print('LOADED:' + ','.join(heavy) if heavy else 'CLEAN')\n"
        )
        self.assertIn("CLEAN", stdout)

    def test_get_api_handler_class_loads_on_demand(self):
        stdout = _run(
            "import sys\n"
            "from gemini_translator.api.factory import get_api_handler_class\n"
            "cls = get_api_handler_class('LocalApiHandler')\n"
            "assert cls.__name__ == 'LocalApiHandler'\n"
            "assert 'gemini_translator.api.handlers.local' in sys.modules\n"
            "assert 'gemini_translator.api.handlers.browser' not in sys.modules\n"
            "from gemini_translator.api import handlers\n"
            "assert handlers.LocalApiHandler is cls\n"
            "print('OK')\n"
        )
        self.assertIn("OK", stdout)

    def test_unknown_handler_still_raises_value_error(self):
        from gemini_translator.api.factory import get_api_handler_class
        with self.assertRaises(ValueError):
            get_api_handler_class("NoSuchApiHandler")


if __name__ == "__main__":
    unittest.main()
