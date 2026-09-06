"""Аудит libs-pymorphy3: pymorphy3 — единственный поддерживаемый бэкенд.

Мёртвая ветка фолбэка на pymorphy2 в gemini_translator/utils/morphology.py
недостижима в реальной среде (pymorphy2 не в requirements, не установлен) и
подлежит удалению. Эти тесты характеризуют новое поведение: модуль больше не
упоминает pymorphy2, а PYMORPHY_AVAILABLE зависит только от pymorphy3.
"""

import importlib
import importlib.util
import inspect
import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gemini_translator.utils import morphology


class Pymorphy2DeadBranchTests(unittest.TestCase):
    def test_source_has_no_pymorphy2_references(self):
        """pymorphy3 — единственный поддерживаемый бэкенд: в исходнике не
        должно остаться ни импорта, ни строк, ни докстринга про pymorphy2."""
        source = inspect.getsource(morphology)
        self.assertNotIn("pymorphy2", source)

    def test_availability_depends_only_on_pymorphy3(self):
        """PYMORPHY_AVAILABLE не должен считать наличие pymorphy2 достаточным:
        если pymorphy3 не найден, доступность должна быть False, даже если
        pymorphy2 "установлен" (что раньше давало True из-за OR в find_spec).

        Модуль исполняется заново в ОТДЕЛЬНОМ объекте (не importlib.reload):
        reload пересоздавал кэш анализатора и лок в живом модуле и ломал
        соседние тесты, сравнивающие общий MorphAnalyzer по identity."""
        real_find_spec = importlib.util.find_spec

        def fake_find_spec(name, *args, **kwargs):
            if name == "pymorphy3":
                return None
            if name == "pymorphy2":
                return mock.Mock(name="fake_pymorphy2_spec")
            return real_find_spec(name, *args, **kwargs)

        spec = importlib.util.spec_from_file_location(
            "gemini_translator.utils._morphology_probe", morphology.__file__
        )
        probe = importlib.util.module_from_spec(spec)
        with mock.patch("importlib.util.find_spec", side_effect=fake_find_spec):
            spec.loader.exec_module(probe)

        self.assertFalse(probe.PYMORPHY_AVAILABLE)
        # Живой модуль не тронут: его состояние и доступность прежние.
        self.assertTrue(morphology.PYMORPHY_AVAILABLE)

if __name__ == "__main__":
    unittest.main()
