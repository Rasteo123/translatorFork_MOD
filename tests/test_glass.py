# tests/test_glass.py
#
# Covers the testable (non-visual) parts of Liquid Glass. The native
# NSVisualEffectView attach is NOT exercised here — manipulating real NSWindows
# in the headless suite can segfault — so the vibrancy hook is mocked. The
# actual frosted effect is validated by eye in the running app.
import unittest

from PyQt6 import QtWidgets

from gemini_translator.ui import themes
from gemini_translator.ui import theme_manager as tm


def test_glass_stylesheet_is_translucent_and_differs():
    base = {"window_bg": "#0f141b", "panel_bg": "#151c24", "accent": "#d87a3a"}
    normal = themes.build_stylesheet(base)
    glass = themes.build_glass_stylesheet(base)
    assert glass != normal
    assert "rgba(" in glass
    assert "background-color: transparent" in glass


def test_glass_available_is_bool():
    # Glass is parked (macos_vibrancy.VIBRANCY_READY is False), so this is False
    # for now even on macOS — just assert a well-formed bool.
    assert isinstance(tm.glass_available(), bool)


class _FakeSettings:
    def __init__(self, data=None):
        self.data = dict(data or {})

    def load_settings(self):
        return dict(self.data)

    def save_settings(self, d):
        self.data = dict(d)
        return True


def test_glass_enabled_defaults_off_and_persists():
    s = _FakeSettings({})
    assert tm.glass_enabled(s) is False  # opt-in
    tm.save_glass(s, True)
    assert s.data[tm.GLASS_KEY] is True
    assert tm.glass_enabled(s) is True


class GlassApplyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_apply_glass_uses_translucent_sheet_and_invokes_hook(self):
        # Mock the native vibrancy hook so no real window surgery happens.
        calls = []
        orig = tm._apply_vibrancy_to_top_levels
        try:
            tm._apply_vibrancy_to_top_levels = (
                lambda app, use_glass: calls.append((app, use_glass))
            )
            tm.apply(self.app, mode="dark", manual_colors={}, glass=True)
            if tm.glass_available():
                self.assertTrue(getattr(self.app, "_glass_active"))
                self.assertIn("rgba(", self.app.styleSheet())
                self.assertEqual(calls, [(self.app, True)])
            else:
                self.assertFalse(getattr(self.app, "_glass_active"))
                self.assertEqual(calls, [(self.app, False)])
        finally:
            tm._apply_vibrancy_to_top_levels = orig
            tm.apply(self.app, mode="dark", manual_colors={}, glass=False)
        self.assertFalse(getattr(self.app, "_glass_active"))
