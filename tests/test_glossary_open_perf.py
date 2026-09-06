"""Пиновка кэшей горячего пути открытия менеджера глоссария.

Заполнение таблицы на N строк вызывало theme_manager.color() тысячи раз, и
до применения темы каждый вызов пересобирал ВСЮ палитру (сотни смешиваний
цветов).
"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets


class FallbackPaletteCacheTests(unittest.TestCase):
    def test_fallback_palette_is_built_once(self):
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        from gemini_translator.ui import theme_manager

        had_palette = getattr(app, "_theme_palette", None)
        if had_palette is not None:
            delattr(app, "_theme_palette")
        try:
            first = theme_manager.palette()
            second = theme_manager.palette()
            self.assertIs(first, second)  # тот же объект — пересборки нет
            self.assertIn("accent_hover_soft", first)
        finally:
            if had_palette is not None:
                app._theme_palette = had_palette


if __name__ == "__main__":
    unittest.main()
