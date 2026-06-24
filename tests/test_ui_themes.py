import unittest

from gemini_translator.ui.themes import (
    DEFAULT_THEME_COLORS,
    THEME_SETTINGS_KEY,
    build_dark_stylesheet,
    build_stylesheet,
    editable_theme_colors,
    extract_theme_colors,
    LIGHT_DEFAULT_THEME_COLORS,
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

    def test_build_stylesheet_replaces_every_template_token(self):
        stylesheet = build_stylesheet(LIGHT_DEFAULT_THEME_COLORS)

        self.assertNotRegex(stylesheet, r"__[A-Z0-9_]+__")
        self.assertIn("QPushButton#reorderButton:hover", stylesheet)

    def test_project_header_action_buttons_have_visible_hover_state(self):
        stylesheet = build_stylesheet(LIGHT_DEFAULT_THEME_COLORS)

        self.assertIn("QPushButton#pathActionButton:hover", stylesheet)
        self.assertIn("QPushButton#compactActionButton:hover", stylesheet)
        self.assertIn("border-color: #d87a3a", stylesheet)

    def test_project_bottom_utility_buttons_share_orange_hover_and_press_states(self):
        stylesheet = build_stylesheet(LIGHT_DEFAULT_THEME_COLORS)

        self.assertIn("QPushButton#projectUtilityButton:hover", stylesheet)
        self.assertIn("QPushButton#ghostActionButton:hover", stylesheet)
        self.assertIn("QPushButton#contextToggleButton:hover", stylesheet)
        self.assertIn("QPushButton#projectUtilityButton:pressed", stylesheet)
        self.assertIn("QPushButton#ghostActionButton:pressed", stylesheet)
        self.assertIn("QPushButton#contextToggleButton:pressed", stylesheet)
        self.assertIn("background-color: rgba(216, 122, 58, 0.12)", stylesheet)

    def test_key_legend_chips_match_real_status_colors(self):
        stylesheet = build_stylesheet(LIGHT_DEFAULT_THEME_COLORS)

        self.assertIn("QLabel#keyLegendChip[state=\"active\"]", stylesheet)
        self.assertIn("QLabel#keyLegendChip[state=\"paused\"]", stylesheet)
        self.assertIn("QLabel#keyLegendChip[state=\"exhausted\"]", stylesheet)
        self.assertIn("color: #1f9d57", stylesheet)
        self.assertIn("color: #b5730a", stylesheet)
        self.assertIn("color: #c0392b", stylesheet)
        self.assertNotIn("QLabel#legendChip[state=\"ok\"]", stylesheet)
        self.assertNotIn("QLabel#legendChip[state=\"warm\"]", stylesheet)
        self.assertNotIn("QLabel#legendChip[state=\"bad\"]", stylesheet)


if __name__ == "__main__":
    unittest.main()
