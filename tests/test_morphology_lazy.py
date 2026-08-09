"""Ленивая морфология: словари pymorphy3 (~35МБ) не строятся при импорте.

Правила:
- utils.morphology.get_morph_analyzer() строит анализатор один раз (кэш),
  потокобезопасно; PYMORPHY_AVAILABLE — дешёвая проверка importability без
  загрузки словарей;
- импорт glossary.py больше НЕ строит анализатор (проверка субпроцессом —
  детерминированно, без зависимости от порядка тестов);
- glued_words использует ОБЩИЙ анализатор (раньше строил второй словарь).
"""

import os
import subprocess
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gemini_translator.utils import morphology


class MorphologyLazyTests(unittest.TestCase):
    def test_probe_available_without_building(self):
        self.assertTrue(morphology.PYMORPHY_AVAILABLE)

    def test_accessor_builds_once_and_caches(self):
        first = morphology.get_morph_analyzer()
        self.assertIsNotNone(first)
        self.assertTrue(hasattr(first, "parse"))
        self.assertIs(morphology.get_morph_analyzer(), first)

    def test_glossary_import_does_not_build_dictionaries(self):
        code = (
            "import os; os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')\n"
            "import gemini_translator.ui.dialogs.validation\n"  # ломает циркулярный импорт
            "import gemini_translator.ui.dialogs.glossary\n"
            "from gemini_translator.utils import morphology\n"
            "print('BUILT' if morphology.morph_analyzer_loaded() else 'LAZY')\n"
        )
        out = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        )
        self.assertEqual(out.returncode, 0, out.stderr[-2000:])
        self.assertIn("LAZY", out.stdout)

    def test_glued_words_uses_shared_analyzer(self):
        from gemini_translator.utils import glued_words
        shared = morphology.get_morph_analyzer()
        self.assertIs(glued_words._default_morph_analyzer(), shared)


if __name__ == "__main__":
    unittest.main()
