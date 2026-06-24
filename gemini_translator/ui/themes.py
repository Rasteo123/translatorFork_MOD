from __future__ import annotations

from pathlib import Path
import re
import sys
from typing import Any


_ASSETS_DIR = Path(__file__).resolve().parent / "assets"
_CHEVRON_DOWN_ICON = (_ASSETS_DIR / "chevron-down.svg").as_posix()
_CHEVRON_UP_ICON = (_ASSETS_DIR / "chevron-up.svg").as_posix()

THEME_SETTINGS_KEY = "ui_theme_colors"
EDITABLE_THEME_COLOR_KEYS = ("window_bg", "panel_bg", "accent")
DEFAULT_THEME_COLORS = {
    "window_bg": "#0f141b",
    "panel_bg": "#151c24",
    "accent": "#d87a3a",
}

DARK_DEFAULT_THEME_COLORS = DEFAULT_THEME_COLORS

LIGHT_DEFAULT_THEME_COLORS = {
    "window_bg": "#f4f4f6",
    "panel_bg": "#ffffff",
    "accent": "#d87a3a",
}

PRESET_BASE_COLORS = {
    "light": LIGHT_DEFAULT_THEME_COLORS,
    "dark": DARK_DEFAULT_THEME_COLORS,
}


def normalize_hex_color(value: Any, fallback: str | None = None) -> str | None:
    if not isinstance(value, str):
        return fallback
    normalized = value.strip()
    if not re.fullmatch(r"#?[0-9a-fA-F]{6}", normalized):
        return fallback
    if not normalized.startswith("#"):
        normalized = f"#{normalized}"
    return normalized.lower()


def sanitize_theme_colors(theme_colors: Any) -> dict[str, str]:
    if not isinstance(theme_colors, dict):
        return {}

    normalized: dict[str, str] = {}
    for key in EDITABLE_THEME_COLOR_KEYS:
        color = normalize_hex_color(theme_colors.get(key))
        if color:
            normalized[key] = color
    return normalized


def extract_theme_colors(settings: Any) -> dict[str, str]:
    if not isinstance(settings, dict):
        return {}
    return sanitize_theme_colors(settings.get(THEME_SETTINGS_KEY))


def editable_theme_colors(theme_colors: Any = None) -> dict[str, str]:
    colors = dict(DEFAULT_THEME_COLORS)
    colors.update(sanitize_theme_colors(theme_colors))
    return colors


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    normalized = normalize_hex_color(color, "#000000") or "#000000"
    return tuple(int(normalized[index:index + 2], 16) for index in (1, 3, 5))


def _rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(channel))) for channel in rgb)


def _mix(color_a: str, color_b: str, amount: float) -> str:
    rgb_a = _hex_to_rgb(color_a)
    rgb_b = _hex_to_rgb(color_b)
    amount = max(0.0, min(1.0, float(amount)))
    mixed = tuple(round((1.0 - amount) * channel_a + amount * channel_b) for channel_a, channel_b in zip(rgb_a, rgb_b))
    return _rgb_to_hex(mixed)


def _luminance(color: str) -> float:
    red, green, blue = _hex_to_rgb(color)
    return ((0.2126 * red) + (0.7152 * green) + (0.0722 * blue)) / 255.0


def _towards_contrast(color: str, amount: float) -> str:
    target = "#ffffff" if _luminance(color) < 0.5 else "#000000"
    return _mix(color, target, amount)


def _towards_shadow(color: str, amount: float) -> str:
    target = "#000000" if _luminance(color) < 0.5 else "#ffffff"
    return _mix(color, target, amount)


def _contrast_text(color: str, light: str = "#ffffff", dark: str = "#0f141b") -> str:
    return light if _luminance(color) < 0.6 else dark


def _status(on_light: str, on_dark: str, window_bg: str) -> str:
    """Pick the status-color variant that reads against the window background."""
    return on_dark if _luminance(window_bg) < 0.5 else on_light


def _rgba(color: str, alpha: float) -> str:
    red, green, blue = _hex_to_rgb(color)
    return f"rgba({red}, {green}, {blue}, {alpha:.2f})"


def build_theme_palette(theme_colors: Any = None) -> dict[str, str]:
    base = editable_theme_colors(theme_colors)
    window_bg = base["window_bg"]
    panel_bg = base["panel_bg"]
    accent = base["accent"]

    text_primary = _contrast_text(window_bg, light="#e6edf5", dark="#10161d")
    title_text = _contrast_text(panel_bg, light="#f6f8fb", dark="#0f141b")
    text_secondary = _mix(text_primary, window_bg, 0.42)
    text_muted = _mix(text_primary, window_bg, 0.58)

    input_bg = _mix(window_bg, panel_bg, 0.38)
    input_disabled_bg = _mix(input_bg, window_bg, 0.52)
    button_bg = _mix(panel_bg, window_bg, 0.26)
    button_hover = _towards_contrast(button_bg, 0.08)
    button_pressed = _towards_shadow(button_bg, 0.08)
    panel_alt_bg = _mix(panel_bg, window_bg, 0.32)
    tab_bg = _mix(window_bg, panel_bg, 0.24)
    list_bg = _mix(window_bg, panel_bg, 0.17)
    list_alt_bg = _mix(list_bg, panel_bg, 0.35)
    chip_bg = _mix(panel_bg, window_bg, 0.34)
    border = _towards_contrast(panel_bg, 0.11)
    border_strong = _towards_contrast(panel_bg, 0.18)
    scroll_handle = _towards_contrast(panel_bg, 0.14)
    scroll_handle_hover = _towards_contrast(panel_bg, 0.22)
    splitter_bg = _mix(window_bg, panel_bg, 0.55)
    accent_text = _contrast_text(accent)

    success = _status("#1f9d57", "#3fd089", window_bg)
    warning = _status("#b5730a", "#e6a23c", window_bg)
    danger = _status("#c0392b", "#ef6b62", window_bg)
    info = _status("#2563c9", "#6aa6ff", window_bg)

    return {
        "window_bg": window_bg,
        "panel_bg": panel_bg,
        "panel_alt_bg": panel_alt_bg,
        "tab_bg": tab_bg,
        "input_bg": input_bg,
        "input_disabled_bg": input_disabled_bg,
        "button_bg": button_bg,
        "button_hover": button_hover,
        "button_pressed": button_pressed,
        "list_bg": list_bg,
        "list_alt_bg": list_alt_bg,
        "chip_bg": chip_bg,
        "border": border,
        "border_strong": border_strong,
        "text_primary": text_primary,
        "text_secondary": text_secondary,
        "text_muted": text_muted,
        "title_text": title_text,
        "accent": accent,
        "accent_hover": _towards_contrast(accent, 0.10),
        "accent_pressed": _towards_shadow(accent, 0.12),
        "accent_text": accent_text,
        "accent_selection_bg": _rgba(accent, 0.24),
        "accent_hover_soft": _rgba(accent, 0.12),
        "accent_soft_bg": _rgba(accent, 0.18),
        "scroll_handle": scroll_handle,
        "scroll_handle_hover": scroll_handle_hover,
        "splitter_bg": splitter_bg,
        "success": success,
        "success_soft_bg": _rgba(success, 0.14),
        "warning": warning,
        "warning_soft_bg": _rgba(warning, 0.14),
        "danger": danger,
        "danger_soft_bg": _rgba(danger, 0.14),
        "info": info,
    }


STYLESHEET_TEMPLATE = """
/* Global surfaces */
QWidget {
    background: transparent;
    color: __TEXT_PRIMARY__;
    font-family: "Segoe UI", sans-serif;
    font-size: 9pt;
}

QDialog,
QMainWindow {
    background-color: __WINDOW_BG__;
}

QLabel,
QCheckBox,
QRadioButton {
    background: transparent;
}

QFrame#projectHeaderCard,
QFrame#projectPathCard,
QFrame#projectStatsCard,
QFrame#projectActionsCard,
QFrame#actionBar,
QFrame#keyTransferColumn,
QFrame#keyPanelSurface,
QWidget#keyTransferColumn,
QWidget#keyPanelSurface,
QFrame#keyStatusCard,
QFrame#statusSurface,
QGroupBox {
    background-color: __PANEL_BG__;
    border: 1px solid __BORDER__;
    border-radius: 12px;
}

QGroupBox {
    margin-top: 12px;
    padding-top: 6px;
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 8px;
    left: 12px;
    color: __ACCENT__;
    font-weight: 600;
}

QLabel#sectionEyebrow,
QLabel#projectCardTitle,
QLabel#mutedCaptionLabel {
    color: __ACCENT__;
    font-size: 9pt;
    font-weight: 600;
    letter-spacing: 0.3px;
}

/* Navigation title "tile": gives the window name (next to ← Назад) a deliberate
   chip in every window so it does not read as bare text on the gray window. */
QLabel#navTitleChip {
    background-color: __PANEL_BG__;
    border: 1px solid __BORDER__;
    border-radius: 8px;
    padding: 5px 14px;
    color: __TITLE_TEXT__;
    font-weight: 600;
}

QLabel#heroTitle {
    color: __TITLE_TEXT__;
    font-size: 13pt;
    font-weight: 600;
}

QLabel#heroSubtitle,
QLabel#projectCardDetail,
QLabel#mutedLabel,
QLabel#helperLabel {
    color: __TEXT_SECONDARY__;
}

QLabel#projectCardValue {
    color: __TITLE_TEXT__;
    font-size: 11pt;
    font-weight: 600;
}

QLabel#metricValueLabel {
    color: __TITLE_TEXT__;
    font-size: 17pt;
    font-weight: 700;
}

QLabel#keyStatusIcon {
    background-color: __ACCENT_SOFT_BG__;
    color: __ACCENT__;
    border-radius: 10px;
    font-size: 15pt;
}

QLabel#keyStatusTitle,
QLabel#keyStatusMetricTitle {
    color: __ACCENT__;
    background: transparent;
    font-size: 9pt;
    font-weight: 600;
}

QLabel#keyStatusTotalValue,
QLabel#keyStatusMetricValue {
    color: __TITLE_TEXT__;
    background: transparent;
    font-size: 15pt;
    font-weight: 700;
}

QLabel#projectStateLabel {
    background-color: __CHIP_BG__;
    border: 1px solid __BORDER_STRONG__;
    border-radius: 12px;
    padding: 6px 12px;
    color: __TEXT_SECONDARY__;
}

QLabel#legendChip {
    background-color: __CHIP_BG__;
    border: 1px solid __BORDER_STRONG__;
    border-radius: 999px;
    padding: 5px 10px;
    color: __TEXT_SECONDARY__;
}

QLabel#projectStateLabel[ready="true"] {
    background-color: rgba(78, 169, 125, 0.16);
    border: 1px solid rgba(78, 169, 125, 0.45);
    color: #8fddb6;
}

QLabel#keyLegendChip {
    background-color: __CHIP_BG__;
    border: 1px solid __BORDER_STRONG__;
    border-radius: 8px;
    padding: 5px 12px;
    font-weight: 600;
}

QLabel#keyLegendChip[state="active"] {
    background-color: __SUCCESS_SOFT_BG__;
    border-color: __SUCCESS__;
    color: __SUCCESS__;
}

QLabel#keyLegendChip[state="paused"] {
    background-color: __WARNING_SOFT_BG__;
    border-color: __WARNING__;
    color: __WARNING__;
}

QLabel#keyLegendChip[state="exhausted"] {
    background-color: __DANGER_SOFT_BG__;
    border-color: __DANGER__;
    color: __DANGER__;
}

/* Tabs */
QTabBar {
    background: transparent;
    border: none;
    padding: 0;
    qproperty-drawBase: 0;
}

QTabWidget::pane {
    border: none;
    background: transparent;
}

QTabWidget::tab-bar {
    background: transparent;
}

QTabBar::scroller {
    width: 0px;
    background: transparent;
    border: none;
}

QTabBar QToolButton {
    background: transparent;
    border: none;
    width: 0px;
    height: 0px;
}

QTabBar::tear {
    width: 0px;
    background: transparent;
    border: none;
    image: none;
}

QTabBar::tab {
    background-color: __PANEL_ALT_BG__;
    color: __TEXT_PRIMARY__;
    border: 1px solid __BORDER_STRONG__;
    border-radius: 10px;
    padding: 8px 16px;
    margin-right: 6px;
    font-weight: 500;
}

QTabBar::tab:selected {
    color: __TITLE_TEXT__;
    background-color: __PANEL_BG__;
    border: 1px solid __ACCENT__;
    font-weight: 700;
}

QTabBar::tab:!selected:hover {
    color: __TEXT_PRIMARY__;
    background-color: __PANEL_BG__;
    border-color: __ACCENT__;
}

QTabBar::tab:disabled {
    color: __TEXT_MUTED__;
    background-color: __INPUT_DISABLED_BG__;
}

/* AI-correction: the nested Данные/Паттерны/Скрытые switcher should read as a
   clean white control — no gray panel behind or under the tabs. */
QTabWidget#correctionOptTabs::pane {
    background: transparent;
}
QTabWidget#correctionOptTabs QTabBar::tab:!selected {
    background-color: __PANEL_BG__;
}

QScrollArea,
QScrollArea > QWidget,
QScrollArea > QWidget > QWidget {
    background: transparent;
    border: none;
}

/* Inputs */
QLineEdit,
QTextEdit,
QPlainTextEdit,
QSpinBox,
QDoubleSpinBox,
QComboBox {
    background-color: __INPUT_BG__;
    color: __TEXT_PRIMARY__;
    border: 1px solid __BORDER__;
    border-radius: 9px;
    padding: 5px 9px;
    selection-background-color: __ACCENT__;
    selection-color: __ACCENT_TEXT__;
}

QLineEdit:focus,
QTextEdit:focus,
QPlainTextEdit:focus,
QSpinBox:focus,
QDoubleSpinBox:focus,
QComboBox:focus {
    border: 1px solid __ACCENT__;
}

QLineEdit:disabled,
QTextEdit:disabled,
QPlainTextEdit:disabled,
QSpinBox:disabled,
QDoubleSpinBox:disabled,
QComboBox:disabled {
    background-color: __INPUT_DISABLED_BG__;
    color: __TEXT_MUTED__;
}

QLineEdit#keySearchField {
    min-height: 18px;
}

QComboBox QAbstractItemView {
    background-color: __PANEL_BG__;
    border: 1px solid __BORDER__;
    border-radius: 10px;
    selection-background-color: __ACCENT__;
    selection-color: __ACCENT_TEXT__;
    outline: 0;
}

QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 30px;
    border: none;
    border-left: 1px solid __BORDER__;
    border-top-right-radius: 8px;
    border-bottom-right-radius: 8px;
    background-color: __BUTTON_BG__;
}

QComboBox::drop-down:hover {
    background-color: __BUTTON_HOVER__;
}

QComboBox::down-arrow {
    image: url("__CHEVRON_DOWN_ICON__");
    width: 12px;
    height: 12px;
    margin-right: 9px;
}

QSpinBox,
QDoubleSpinBox {
    padding-right: 30px;
}

QSpinBox::up-button,
QDoubleSpinBox::up-button {
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 28px;
    border: none;
    border-left: 1px solid __BORDER__;
    border-top-right-radius: 8px;
    background-color: __BUTTON_BG__;
}

QSpinBox::down-button,
QDoubleSpinBox::down-button {
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: 28px;
    border: none;
    border-left: 1px solid __BORDER__;
    border-bottom-right-radius: 8px;
    background-color: __BUTTON_BG__;
}

QSpinBox::up-button:hover,
QSpinBox::down-button:hover,
QDoubleSpinBox::up-button:hover,
QDoubleSpinBox::down-button:hover {
    background-color: __BUTTON_HOVER__;
}

QSpinBox::up-arrow,
QDoubleSpinBox::up-arrow {
    image: url("__CHEVRON_UP_ICON__");
    width: 9px;
    height: 9px;
}

QSpinBox::down-arrow,
QDoubleSpinBox::down-arrow {
    image: url("__CHEVRON_DOWN_ICON__");
    width: 9px;
    height: 9px;
}

QMenu {
    background-color: __PANEL_BG__;
    color: __TEXT_PRIMARY__;
    border: 1px solid __BORDER__;
    border-radius: 10px;
    padding: 5px;
}

QMenu::item {
    background: transparent;
    border-radius: 7px;
    padding: 6px 22px 6px 10px;
}

QMenu::item:selected {
    background-color: __ACCENT_HOVER_SOFT__;
    color: __TEXT_PRIMARY__;
}

QMenu::separator {
    height: 1px;
    background-color: __BORDER__;
    margin: 5px 8px;
}

/* Buttons */
QPushButton {
    background-color: __BUTTON_BG__;
    color: __TEXT_PRIMARY__;
    border: 1px solid __BORDER_STRONG__;
    border-radius: 9px;
    padding: 6px 12px;
}

QPushButton:hover {
    background-color: __BUTTON_HOVER__;
    border-color: __BORDER_STRONG__;
}

QPushButton:pressed {
    background-color: __BUTTON_PRESSED__;
}

QPushButton:disabled {
    background-color: __INPUT_DISABLED_BG__;
    color: __TEXT_MUTED__;
    border-color: __BORDER__;
}

QPushButton#primaryActionButton {
    background-color: __ACCENT__;
    color: __ACCENT_TEXT__;
    border: 1px solid __ACCENT__;
    font-weight: 700;
    padding: 8px 16px;
}

QPushButton#primaryActionButton:hover {
    background-color: __ACCENT_HOVER__;
    border-color: __ACCENT_HOVER__;
}

QPushButton#primaryActionButton:pressed {
    background-color: __ACCENT_PRESSED__;
    border-color: __ACCENT_PRESSED__;
}

QPushButton#dangerActionButton {
    background-color: #412026;
    color: #ffd8d8;
    border: 1px solid #7a3945;
    font-weight: 600;
}

QPushButton#dangerActionButton:hover {
    background-color: #542630;
    border-color: #93424f;
}

QPushButton#ghostActionButton,
QPushButton#compactActionButton,
QPushButton#projectUtilityButton,
QPushButton#pathActionButton {
    background-color: __INPUT_BG__;
}

QPushButton#pathActionButton {
    font-weight: 600;
}

QPushButton#ghostActionButton:hover,
QPushButton#compactActionButton:hover,
QPushButton#projectUtilityButton:hover,
QPushButton#pathActionButton:hover,
QPushButton#contextToggleButton:hover {
    background-color: __ACCENT_HOVER_SOFT__;
    border-color: __ACCENT__;
    color: __TEXT_PRIMARY__;
}

QPushButton#ghostActionButton:pressed,
QPushButton#compactActionButton:pressed,
QPushButton#projectUtilityButton:pressed,
QPushButton#pathActionButton:pressed,
QPushButton#contextToggleButton:pressed {
    background-color: __ACCENT_SOFT_BG__;
    border-color: __ACCENT__;
    color: __TEXT_PRIMARY__;
}

QPushButton#contextToggleButton {
    background-color: __INPUT_BG__;
    padding: 7px 12px;
}

QPushButton#contextToggleButton:checked {
    background-color: __ACCENT_SOFT_BG__;
    border: 1px solid __ACCENT__;
    color: __TEXT_PRIMARY__;
}

QPushButton#ghostActionButton:disabled,
QPushButton#ghostActionButton:disabled:hover,
QPushButton#ghostActionButton:disabled:pressed,
QPushButton#compactActionButton:disabled,
QPushButton#compactActionButton:disabled:hover,
QPushButton#compactActionButton:disabled:pressed,
QPushButton#projectUtilityButton:disabled,
QPushButton#projectUtilityButton:disabled:hover,
QPushButton#projectUtilityButton:disabled:pressed,
QPushButton#pathActionButton:disabled,
QPushButton#pathActionButton:disabled:hover,
QPushButton#pathActionButton:disabled:pressed,
QPushButton#contextToggleButton:disabled,
QPushButton#contextToggleButton:disabled:hover,
QPushButton#contextToggleButton:disabled:pressed {
    background-color: __INPUT_DISABLED_BG__;
    color: __TEXT_MUTED__;
    border-color: __BORDER__;
}

QPushButton#reorderButton {
    background-color: __INPUT_BG__;
    color: __TEXT_PRIMARY__;
    border: 1px solid __BORDER__;
    border-radius: 4px;
    font-size: 12px;
    padding: 0px;
}

QPushButton#reorderButton:hover {
    background-color: __ACCENT_HOVER_SOFT__;
    border-color: __BORDER_STRONG__;
}

/* Lists and tables */
QTableWidget,
QListWidget {
    background-color: __LIST_BG__;
    alternate-background-color: __LIST_ALT_BG__;
    border: 1px solid __BORDER__;
    border-radius: 12px;
    gridline-color: __BORDER__;
    outline: 0;
}

QListWidget#keyListWidget {
    padding: 6px;
}

QListWidget#keyListWidget::item {
    padding: 6px 9px;
    margin: 2px 0;
    border-radius: 8px;
}

QListWidget::item:selected,
QTableWidget::item:selected {
    background-color: __ACCENT_SELECTION_BG__;
    color: __ACCENT_TEXT__;
}

QListWidget::item:hover {
    background-color: __ACCENT_HOVER_SOFT__;
}

QHeaderView::section {
    background-color: __TAB_BG__;
    color: __TEXT_SECONDARY__;
    border: none;
    border-bottom: 1px solid __BORDER__;
    padding: 8px 6px;
    font-weight: 600;
}

/* Scroll bars */
QScrollBar:vertical {
    border: none;
    background: transparent;
    width: 11px;
    margin: 2px;
}

QScrollBar::handle:vertical {
    background: __SCROLL_HANDLE__;
    min-height: 28px;
    border-radius: 5px;
}

QScrollBar::handle:vertical:hover {
    background: __SCROLL_HANDLE_HOVER__;
}

QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical {
    height: 0;
}

QScrollBar:horizontal {
    border: none;
    background: transparent;
    height: 11px;
    margin: 2px;
}

QScrollBar::handle:horizontal {
    background: __SCROLL_HANDLE__;
    min-width: 28px;
    border-radius: 5px;
}

QScrollBar::handle:horizontal:hover {
    background: __SCROLL_HANDLE_HOVER__;
}

QScrollBar::add-line:horizontal,
QScrollBar::sub-line:horizontal {
    width: 0;
}

/* Splitters */
QSplitter::handle {
    background-color: __SPLITTER_BG__;
    border: 1px solid __WINDOW_BG__;
}

QSplitter::handle:horizontal {
    width: 8px;
}

QSplitter::handle:vertical {
    height: 8px;
}

QSplitter::handle:hover {
    background-color: __ACCENT__;
}

/* Progress and checkboxes */
QProgressBar {
    border: 1px solid __BORDER__;
    border-radius: 10px;
    text-align: center;
    background-color: __INPUT_BG__;
    color: __TITLE_TEXT__;
    min-height: 20px;
}

QProgressBar::chunk {
    background-color: __ACCENT__;
    border-radius: 8px;
}

QCheckBox {
    spacing: 8px;
}

QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 1px solid __BORDER_STRONG__;
    background-color: __LIST_BG__;
}

QCheckBox::indicator:checked {
    background-color: __ACCENT__;
    border-color: __ACCENT__;
}

/* Home tool picker */
QLabel#homeHeading {
    color: __TITLE_TEXT__;
    background: transparent;
    font-size: 15pt;
    font-weight: 600;
}

QFrame#toolCard,
QFrame#toolHeroCard {
    background-color: __PANEL_BG__;
    border: 1px solid __BORDER__;
    border-radius: 14px;
}

QFrame#toolCard:hover,
QFrame#toolHeroCard:hover {
    border-color: __ACCENT__;
    background-color: __PANEL_ALT_BG__;
}

QLabel#toolIconTile {
    background-color: __ACCENT_SOFT_BG__;
    color: __ACCENT__;
    border-radius: 11px;
    font-size: 18pt;
}

QLabel#toolHeroTitle {
    color: __TITLE_TEXT__;
    background: transparent;
    font-size: 13pt;
    font-weight: 600;
}

QLabel#toolCardTitle {
    color: __TITLE_TEXT__;
    background: transparent;
    font-size: 11pt;
    font-weight: 600;
}

QLabel#toolCardDetail {
    color: __TEXT_SECONDARY__;
    background: transparent;
    font-size: 9pt;
}

QLabel#toolOpenPill {
    background-color: __ACCENT__;
    color: __ACCENT_TEXT__;
    border-radius: 9px;
    padding: 9px 18px;
    font-weight: 600;
}

QToolTip {
    background-color: __PANEL_ALT_BG__;
    color: __TEXT_PRIMARY__;
    border: 1px solid __BORDER_STRONG__;
    padding: 6px 8px;
    border-radius: 8px;
}

QMessageBox {
    background-color: __WINDOW_BG__;
}

QMessageBox QLabel {
    color: __TEXT_PRIMARY__;
}
"""


def build_dark_stylesheet(theme_colors: Any = None) -> str:
    palette = build_theme_palette(theme_colors)
    stylesheet = STYLESHEET_TEMPLATE

    if sys.platform == "darwin":
        stylesheet += """
        /* macOS Table Overrides */
        QTableWidget, QListWidget {
            background-color: __LIST_BG__;
            border: 1px solid __BORDER__;
            border-radius: 8px;
            gridline-color: transparent;
            selection-background-color: transparent;
            selection-color: __TEXT_PRIMARY__;
            outline: 0;
        }
        QTableWidget::item, QListWidget::item {
            border: 1px solid transparent;
            border-radius: 6px;
            margin: 2px 4px;
            padding: 2px 8px;
            min-height: 28px;
        }
        QTableWidget::item:hover, QListWidget::item:hover {
            background-color: __ACCENT_HOVER_SOFT__;
        }
        QTableWidget::item:selected, QListWidget::item:selected {
            background-color: __ACCENT_SOFT_BG__;
            color: __TEXT_PRIMARY__;
        }
        QHeaderView::section:horizontal {
            background-color: transparent;
            color: __TEXT_SECONDARY__;
            border: none;
            border-bottom: 1px solid __BORDER__;
            padding: 6px 8px;
            font-weight: 600;
        }
        QHeaderView::section:vertical {
            background-color: transparent;
            color: __TEXT_SECONDARY__;
            border: none;
            border-right: 1px solid __BORDER__;
            padding: 0px 8px;
            font-weight: 600;
        }
        """
    else:
        stylesheet += """
        /* Windows/Linux Table Overrides */
        QTableWidget, QListWidget {
            background-color: __LIST_BG__;
            alternate-background-color: __LIST_ALT_BG__;
            border: 1px solid __BORDER__;
            border-radius: 8px;
            gridline-color: __BORDER__;
            selection-background-color: transparent;
            selection-color: __TEXT_PRIMARY__;
            outline: 0;
        }
        QTableWidget::item, QListWidget::item {
            border-radius: 6px;
            margin: 2px 4px;
            padding: 2px 8px;
            min-height: 28px;
        }
        QTableWidget::item:hover, QListWidget::item:hover {
            background-color: __ACCENT_HOVER_SOFT__;
        }
        QTableWidget::item:selected, QListWidget::item:selected {
            background-color: __ACCENT_SOFT_BG__;
            color: __TEXT_PRIMARY__;
        }
        QHeaderView::section:horizontal {
            background-color: __TAB_BG__;
            color: __TEXT_SECONDARY__;
            border: none;
            border-bottom: 2px solid __BORDER__;
            border-right: 1px solid __BORDER__;
            padding: 8px 12px;
            font-weight: 600;
        }
        QHeaderView::section:vertical {
            background-color: __TAB_BG__;
            color: __TEXT_SECONDARY__;
            border: none;
            border-right: 1px solid __BORDER__;
            padding: 0px 8px;
            font-weight: 600;
        }
        """

    for key, value in palette.items():
        stylesheet = stylesheet.replace(f"__{key.upper()}__", value)
    stylesheet = stylesheet.replace("__CHEVRON_DOWN_ICON__", _CHEVRON_DOWN_ICON)
    stylesheet = stylesheet.replace("__CHEVRON_UP_ICON__", _CHEVRON_UP_ICON)
    return stylesheet


DARK_STYLESHEET = build_dark_stylesheet()


def build_stylesheet(theme_colors: Any = None) -> str:
    """Build the QSS for a complete base-color set (light or dark).

    Thin alias over :func:`build_dark_stylesheet`; the latter renders any
    complete ``{window_bg, panel_bg, accent}`` mapping, so the name is kept
    only for backward compatibility.
    """
    return build_dark_stylesheet(theme_colors)


GLASS_OVERRIDE_TEMPLATE = """
/* === Liquid Glass (macOS vibrancy) overrides === */
QWidget {
    background-color: transparent;
}
QDialog, QMainWindow {
    background: transparent;
}
QFrame#projectHeaderCard,
QFrame#projectPathCard,
QFrame#projectStatsCard,
QFrame#projectActionsCard,
QFrame#actionBar,
QFrame#keyTransferColumn,
QFrame#keyPanelSurface,
QWidget#keyTransferColumn,
QWidget#keyPanelSurface,
QFrame#keyStatusCard,
QFrame#statusSurface,
QGroupBox {
    background-color: __PANEL_GLASS__;
    border: 1px solid __BORDER__;
}
QLineEdit,
QTextEdit,
QPlainTextEdit,
QSpinBox,
QDoubleSpinBox,
QComboBox {
    background-color: __INPUT_GLASS__;
}
QTableWidget,
QListWidget {
    background-color: __LIST_GLASS__;
}
QTabWidget::pane {
    background-color: __TAB_GLASS__;
}
QMenu {
    background-color: __MENU_GLASS__;
    border: 1px solid __BORDER_STRONG__;
}
"""


def build_glass_stylesheet(theme_colors: Any = None, opacities: dict | None = None) -> str:
    """Base stylesheet + a translucent 'glass' override layer.

    Meant to pair with a macOS NSVisualEffectView backdrop: the top-level
    surfaces go transparent so the frosted vibrancy shows, and panels/inputs
    become semi-transparent. Off macOS it is still a valid (just flat-looking)
    stylesheet, so callers may use it unconditionally.
    """
    palette = build_theme_palette(theme_colors)
    opacities = opacities or {}

    panel_op = opacities.get("panel", 0.55)
    input_op = opacities.get("input", 0.50)
    list_op = opacities.get("list", 0.42)
    tab_op = opacities.get("tab", 0.38)

    glass = {
        "panel_glass": _rgba(palette["panel_bg"], panel_op),
        "input_glass": _rgba(palette["input_bg"], input_op),
        "list_glass": _rgba(palette["list_bg"], list_op),
        "tab_glass": _rgba(palette["tab_bg"], tab_op),
        "menu_glass": _rgba(palette["panel_bg"], 0.88),
        "border": palette["border"],
        "border_strong": palette["border_strong"],
    }
    overrides = GLASS_OVERRIDE_TEMPLATE
    for key, value in glass.items():
        overrides = overrides.replace(f"__{key.upper()}__", value)
    return build_dark_stylesheet(theme_colors) + "\n" + overrides
