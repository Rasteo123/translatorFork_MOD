# gemini_translator/ui/theme_manager.py
"""Single owner of application theming (light / dark / auto + manual override).

The resolution logic is pure (no Qt) so it can be unit-tested directly; the
Qt-touching parts are thin wrappers at the bottom of the module.
"""
from __future__ import annotations

from typing import Any

THEME_MODE_KEY = "ui_theme_mode"
VALID_MODES = ("light", "dark", "auto", "custom")
DEFAULT_MODE = "auto"


def normalize_mode(value: Any, fallback: str = DEFAULT_MODE) -> str:
    return value if value in VALID_MODES else fallback


def resolve_scheme(mode: str, system_is_dark: bool) -> str:
    """Map a theme mode to a concrete scheme name ('light' or 'dark')."""
    mode = normalize_mode(mode)
    if mode == "light":
        return "light"
    if mode == "dark":
        return "dark"
    return "dark" if system_is_dark else "light"


def custom_overrides(manual_colors: dict | None) -> dict[str, str]:
    """Manual colours minus legacy preset-equal BACKGROUNDS.

    The old appearance dialog auto-saved the dark defaults; a window_bg/panel_bg
    equal to a preset default is a legacy value (not a deliberate choice) that
    would pin the theme to dark, so it is dropped. The accent is always kept — it
    does not pin light/dark and is the app's brand colour.
    """
    from .themes import (
        sanitize_theme_colors,
        DARK_DEFAULT_THEME_COLORS,
        LIGHT_DEFAULT_THEME_COLORS,
    )

    out: dict[str, str] = {}
    for key, value in sanitize_theme_colors(manual_colors).items():
        if key in ("window_bg", "panel_bg") and (
            value == DARK_DEFAULT_THEME_COLORS.get(key)
            or value == LIGHT_DEFAULT_THEME_COLORS.get(key)
        ):
            continue
        out[key] = value
    return out


def resolve_base_colors(
    scheme: str,
    manual_colors: dict | None = None,
    system_accent: str | None = None,
) -> dict[str, str]:
    """Return a complete {window_bg, panel_bg, accent} mapping for a scheme.

    Layering: preset -> system accent (only if accent not overridden) -> genuine
    custom overrides (preset-equal legacy values are dropped; see custom_overrides).
    """
    from .themes import PRESET_BASE_COLORS

    base = dict(PRESET_BASE_COLORS.get(scheme, PRESET_BASE_COLORS["dark"]))
    manual = custom_overrides(manual_colors)
    # Keep the app's standard orange accent unless the user explicitly overrides it.
    base.update(manual)
    return base


def system_is_dark(app) -> bool:
    """True when the OS color scheme is dark. Defaults to dark on failure."""
    try:
        from PyQt6.QtCore import Qt
        return app.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except Exception:
        return True


def system_accent(app) -> str | None:
    """The OS accent color as #rrggbb (macOS controlAccentColor), or None."""
    try:
        from PyQt6.QtGui import QPalette
        color = app.palette().color(QPalette.ColorRole.Highlight)
        return color.name() if color.isValid() else None
    except Exception:
        return None


def apply(
    app,
    mode: str | None = None,
    manual_colors: dict | None = None,
    glass: bool = False,
    glass_opacities: dict | None = None,
) -> str:
    """Resolve and install the stylesheet for ``mode`` onto ``app``.

    When ``glass`` is True and macOS vibrancy is available, installs the
    translucent glass stylesheet and attaches an NSVisualEffectView backdrop to
    visible top-level windows. Returns the concrete scheme ('light'/'dark').
    """
    from .themes import build_stylesheet, build_glass_stylesheet, build_theme_palette

    scheme = resolve_scheme(normalize_mode(mode), system_is_dark(app))
    base = resolve_base_colors(scheme, manual_colors, system_accent(app))
    use_glass = bool(glass) and glass_available()
    app.setStyleSheet(
        build_glass_stylesheet(base, glass_opacities) if use_glass else build_stylesheet(base)
    )
    setattr(app, "_active_theme_mode", normalize_mode(mode))
    setattr(app, "_theme_palette", build_theme_palette(base))
    setattr(app, "_glass_active", use_glass)
    _apply_vibrancy_to_top_levels(app, use_glass)
    return scheme


def migrate_theme_mode(settings: dict | None) -> str:
    """Decide the theme mode for a settings dict that may predate theme modes.

    - explicit ``ui_theme_mode`` wins (normalized);
    - a non-empty legacy profile (used the app before modes existed) stays on
      ``dark`` so current users are not surprised by a sudden light theme;
    - an empty/new profile starts on ``auto``.
    """
    if not isinstance(settings, dict) or not settings:
        return DEFAULT_MODE
    if THEME_MODE_KEY in settings:
        return normalize_mode(settings.get(THEME_MODE_KEY))

    from .themes import THEME_SETTINGS_KEY
    if settings.get(THEME_SETTINGS_KEY):
        return "custom"

    return "dark"


def _read_settings(settings_manager) -> dict:
    for name in ("load_full_session_settings", "load_settings"):
        loader = getattr(settings_manager, name, None)
        if callable(loader):
            try:
                data = loader()
            except Exception:
                continue
            if isinstance(data, dict) and data:
                return data
    return {}


def _declares_attr(obj, name: str) -> bool:
    if name in vars(obj):
        return True
    return any(name in vars(cls) for cls in type(obj).__mro__)


def _settings_method(settings_manager, preferred: str, fallback: str):
    name = preferred if _declares_attr(settings_manager, preferred) else fallback
    return getattr(settings_manager, name, None)


def load_mode(settings_manager) -> str:
    return migrate_theme_mode(_read_settings(settings_manager))


def save_mode(settings_manager, mode: str) -> None:
    saver = _settings_method(settings_manager, "save_full_session_settings", "save_settings")
    loader = _settings_method(settings_manager, "load_full_session_settings", "load_settings")

    if not callable(saver) or not callable(loader):
        return
    try:
        data = loader()
        data = dict(data) if isinstance(data, dict) else {}
        data[THEME_MODE_KEY] = normalize_mode(mode)
        saver(data)
    except Exception:
        pass


def install(app, settings_manager) -> None:
    """Connect the OS scheme-change signal so 'auto' re-applies live."""
    def _on_scheme_changed(*_args):
        if getattr(app, "_active_theme_mode", DEFAULT_MODE) == "auto":
            manual = _manual_colors(settings_manager)
            apply(app, mode="auto", manual_colors=manual)

    try:
        app.styleHints().colorSchemeChanged.connect(_on_scheme_changed)
    except Exception:
        pass


def _manual_colors(settings_manager) -> dict:
    from .themes import extract_theme_colors
    return extract_theme_colors(_read_settings(settings_manager))


def palette(app=None) -> dict:
    """The active resolved palette (~30 tokens). Falls back to the dark preset."""
    from PyQt6.QtWidgets import QApplication
    from .themes import build_theme_palette, DARK_DEFAULT_THEME_COLORS

    app = app or QApplication.instance()
    pal = getattr(app, "_theme_palette", None) if app is not None else None
    return pal if isinstance(pal, dict) else build_theme_palette(DARK_DEFAULT_THEME_COLORS)


def color(name: str, app=None) -> str:
    """A single token's current hex; safe fallback for unknown names."""
    return palette(app).get(name, "#888888")


GLASS_KEY = "ui_glass_enabled"


def glass_available() -> bool:
    """True only on macOS with pyobjc AND when vibrancy is enabled.

    Gated on macos_vibrancy.VIBRANCY_READY, currently False while the Qt
    integration is refined — so the toggle stays hidden and glass never applies.
    """
    try:
        from .platform import macos_vibrancy
        return macos_vibrancy.is_available() and macos_vibrancy.VIBRANCY_READY
    except Exception:
        return False


def glass_enabled(settings_manager) -> bool:
    """Whether the user opted into Liquid Glass (default off — opt-in)."""
    return bool(_read_settings(settings_manager).get(GLASS_KEY, False))


def save_glass(settings_manager, on: bool) -> None:
    saver = _settings_method(settings_manager, "save_full_session_settings", "save_settings")
    loader = _settings_method(settings_manager, "load_full_session_settings", "load_settings")

    if not callable(saver) or not callable(loader):
        return
    try:
        data = loader()
        data = dict(data) if isinstance(data, dict) else {}
        data[GLASS_KEY] = bool(on)
        saver(data)
    except Exception:
        pass


def apply_window_glass(widget) -> bool:
    """Attach the macOS vibrancy backdrop to a single top-level window."""
    try:
        from .platform import macos_vibrancy
        return macos_vibrancy.apply_vibrancy(widget)
    except Exception:
        return False


def _apply_vibrancy_to_top_levels(app, use_glass: bool) -> None:
    try:
        from .platform import macos_vibrancy
        for widget in app.topLevelWidgets():
            try:
                if widget.isVisible():
                    if use_glass:
                        macos_vibrancy.apply_vibrancy(widget)
                    else:
                        macos_vibrancy.remove_vibrancy(widget)
            except Exception:
                continue
    except Exception:
        pass

def load_glass_opacities(settings_manager) -> dict:
    data = _read_settings(settings_manager)
    return dict(data.get("glass_opacities", {}))

def save_glass_opacities(settings_manager, opacities: dict) -> None:
    saver = _settings_method(settings_manager, "save_full_session_settings", "save_settings")
    loader = _settings_method(settings_manager, "load_full_session_settings", "load_settings")

    if not callable(saver) or not callable(loader):
        return
    try:
        data = loader()
        data = dict(data) if isinstance(data, dict) else {}
        data["glass_opacities"] = dict(opacities)
        saver(data)
    except Exception:
        pass
