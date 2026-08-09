"""Пиновка кэшей горячего пути открытия менеджера глоссария.

Заполнение таблицы на N строк вызывало theme_manager.color() тысячи раз, и
до применения темы каждый вызов пересобирал ВСЮ палитру (сотни смешиваний
цветов), а style().standardIcon дёргался на каждую кнопку каждой строки.
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


class StdIconCacheTests(unittest.TestCase):
    def test_std_icon_cached_per_pixmap(self):
        QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        import gemini_translator.ui.dialogs.validation  # порядок импорта
        from gemini_translator.ui.dialogs.glossary import GlossaryManagerPage
        from PyQt6.QtWidgets import QStyle

        calls = []

        class _Host:
            _std_icon = GlossaryManagerPage._std_icon

            def style(self):
                host = self

                class _Style:
                    def standardIcon(self, pixmap):
                        calls.append(pixmap)
                        return object()
                return _Style()

        host = _Host()
        icon1 = host._std_icon(QStyle.StandardPixmap.SP_TrashIcon)
        icon2 = host._std_icon(QStyle.StandardPixmap.SP_TrashIcon)
        self.assertIs(icon1, icon2)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
