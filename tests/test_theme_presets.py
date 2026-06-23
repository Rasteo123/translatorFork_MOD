# tests/test_theme_presets.py
from gemini_translator.ui import themes


def test_light_preset_is_brighter_than_dark():
    light = themes.LIGHT_DEFAULT_THEME_COLORS
    dark = themes.DARK_DEFAULT_THEME_COLORS
    assert set(light) == {"window_bg", "panel_bg", "accent"}
    assert themes._luminance(light["window_bg"]) > themes._luminance(dark["window_bg"])


def test_presets_map_has_both_schemes():
    assert themes.PRESET_BASE_COLORS["light"] == themes.LIGHT_DEFAULT_THEME_COLORS
    assert themes.PRESET_BASE_COLORS["dark"] == themes.DARK_DEFAULT_THEME_COLORS


def test_light_stylesheet_uses_light_window_bg():
    sheet = themes.build_stylesheet(themes.LIGHT_DEFAULT_THEME_COLORS)
    dark_sheet = themes.build_stylesheet(themes.DARK_DEFAULT_THEME_COLORS)
    assert f"background-color: {themes.LIGHT_DEFAULT_THEME_COLORS['window_bg']}" in sheet
    assert sheet != dark_sheet


def test_stylesheet_modernizes_native_input_subcontrols():
    sheet = themes.build_stylesheet(themes.LIGHT_DEFAULT_THEME_COLORS)

    assert "QTabBar {" in sheet
    assert "QTabBar {\n    background: transparent;" in sheet
    assert "QTabBar::tab:selected" in sheet
    assert "QLabel,\nQCheckBox,\nQRadioButton {\n    background: transparent;" in sheet
    assert "QComboBox::drop-down" in sheet
    assert "QComboBox::down-arrow" in sheet
    assert "QSpinBox::up-button" in sheet
    assert "QSpinBox::down-button" in sheet
    assert "QDoubleSpinBox::up-button" in sheet
    assert "QDoubleSpinBox::down-button" in sheet
    assert "QMenu::item:selected" in sheet
    assert "chevron-down.svg" in sheet
    assert "chevron-up.svg" in sheet
    assert "keyStatusDetail" not in sheet
