import unittest

from gemini_translator.ui.themes import (
    DEFAULT_THEME_COLORS,
    THEME_SETTINGS_KEY,
    build_dark_stylesheet,
    editable_theme_colors,
    extract_theme_colors,
)


class UiThemeTests(unittest.TestCase):
    def test_editable_theme_colors_merges_defaults_and_normalizes_hex(self):
        colors = editable_theme_colors(
            {
                "window_bg": "112233",
                "accent": "#ABCDEF",
            }
        )

        self.assertEqual(colors["window_bg"], "#112233")
        self.assertEqual(colors["accent"], "#abcdef")
        self.assertEqual(colors["panel_bg"], DEFAULT_THEME_COLORS["panel_bg"])

    def test_extract_theme_colors_ignores_invalid_payload(self):
        extracted = extract_theme_colors(
            {
                THEME_SETTINGS_KEY: {
                    "window_bg": "not-a-color",
                    "accent": "#ff8800",
                }
            }
        )

        self.assertEqual(extracted, {"accent": "#ff8800"})

    def test_build_dark_stylesheet_includes_custom_palette_values(self):
        stylesheet = build_dark_stylesheet(
            {
                "window_bg": "#112233",
                "panel_bg": "#223344",
                "accent": "#ff8800",
            }
        )

        self.assertIn("#112233", stylesheet)
        self.assertIn("#223344", stylesheet)
        self.assertIn("#ff8800", stylesheet)
        self.assertNotIn("__ACCENT__", stylesheet)


if __name__ == "__main__":
    unittest.main()
