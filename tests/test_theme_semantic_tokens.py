# tests/test_theme_semantic_tokens.py
import unittest

from PyQt6 import QtWidgets

from gemini_translator.ui import themes
from gemini_translator.ui import theme_manager as tm


def _pal(window_bg):
    return themes.build_theme_palette(
        {"window_bg": window_bg, "panel_bg": window_bg, "accent": "#d87a3a"}
    )


def test_semantic_tokens_present():
    pal = _pal("#0f141b")
    for key in ("success", "warning", "danger", "info"):
        assert key in pal and pal[key].startswith("#")


def test_status_tokens_adapt_to_base_lightness():
    dark = _pal("#0f141b")
    light = _pal("#f4f4f6")
    # On a dark window the success green is lighter than on a light window.
    assert themes._luminance(dark["success"]) > themes._luminance(light["success"])


class PaletteHelperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_color_reflects_applied_mode(self):
        tm.apply(self.app, mode="light", manual_colors={})
        light_muted = tm.color("text_muted", self.app)
        tm.apply(self.app, mode="dark", manual_colors={})
        dark_muted = tm.color("text_muted", self.app)
        self.assertTrue(light_muted.startswith("#") and dark_muted.startswith("#"))
        self.assertNotEqual(light_muted, dark_muted)

    def test_color_has_semantic_tokens_and_fallback(self):
        tm.apply(self.app, mode="dark", manual_colors={})
        self.assertTrue(tm.color("success", self.app).startswith("#"))
        # Unknown token returns a safe fallback string, not a crash.
        self.assertIsInstance(tm.color("does_not_exist", self.app), str)
