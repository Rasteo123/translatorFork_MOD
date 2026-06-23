"""macOS window vibrancy ("liquid glass" frosted backdrop) via NSVisualEffectView.

Best-effort and macOS-only. Every public call is a safe no-op off macOS, when
pyobjc is missing, or when the Qt platform is not the native ``cocoa`` plugin
(e.g. ``offscreen`` in headless tests) — so callers never need to guard, and the
native window surgery never runs in a context where it could fault. On failure
it records ``last_error`` (a human-readable string) instead of raising, because
the visual effect can only be validated by eye and a failed attach must surface
a reason.
"""
from __future__ import annotations

import sys

#: Last failure reason (or None). Set by apply_vibrancy/is_available on error.
last_error: str | None = None

#: Master switch. We fixed the NSVisualEffectView integration to use superview
#: insertion instead of reparenting, so ghosting is resolved.
# Gated until PyQt6 integration is fully stable across all themes and window states.
# Currently set to False per user request to isolate Liquid Glass.
VIBRANCY_READY = False

# Material name -> AppKit constant attribute name. Resolved lazily so the module
# imports fine without pyobjc.
_MATERIALS = {
    "sidebar": "NSVisualEffectMaterialSidebar",
    "window": "NSVisualEffectMaterialWindowBackground",
    "under_window": "NSVisualEffectMaterialUnderWindowBackground",
    "hud": "NSVisualEffectMaterialHUDWindow",
}


def is_available() -> bool:
    """True only on macOS with pyobjc (objc + AppKit) importable."""
    global last_error
    if sys.platform != "darwin":
        return False
    try:
        import objc  # noqa: F401
        from AppKit import NSVisualEffectView  # noqa: F401
        return True
    except Exception as exc:
        last_error = f"pyobjc unavailable: {exc!r}"
        return False


def _is_cocoa() -> bool:
    try:
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        return app is not None and app.platformName() == "cocoa"
    except Exception:
        return False


def apply_vibrancy(widget, material: str = "window") -> bool:
    """Attach an NSVisualEffectView behind ``widget``'s top-level NSWindow.

    ``widget`` must already be shown (so ``winId()`` maps to a live NSWindow).
    No-op (returns False) unless on macOS, with pyobjc, on the cocoa platform.
    Records ``last_error`` on failure.
    """
    global last_error
    if not is_available():
        return False
    if not _is_cocoa():
        last_error = "vibrancy needs the native cocoa platform (not offscreen/minimal)"
        return False
    try:
        import objc
        import AppKit
        from PyQt6.QtCore import Qt

        view = objc.objc_object(c_void_p=int(widget.winId()))
        window = view.window()
        if window is None:
            last_error = "winId() has no NSWindow yet — call after the window is shown"
            return False

        content = window.contentView()
        superview = content.superview()

        # Check if we already added an effect view
        existing_effect = None
        for subview in superview.subviews() if superview is not None else content.subviews():
            if isinstance(subview, AppKit.NSVisualEffectView):
                existing_effect = subview
                break

        if existing_effect is None:
            effect = AppKit.NSVisualEffectView.alloc().initWithFrame_(content.bounds())
            effect.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
            material_const = getattr(AppKit, _MATERIALS.get(material, _MATERIALS["window"]), None)
            if material_const is not None:
                effect.setMaterial_(material_const)
            effect.setBlendingMode_(AppKit.NSVisualEffectBlendingModeBehindWindow)
            effect.setState_(AppKit.NSVisualEffectStateActive)

            if superview is not None:
                superview.addSubview_positioned_relativeTo_(effect, AppKit.NSWindowBelow, content)
            else:
                window.setContentView_(effect)
                effect.addSubview_(content)

        widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        window.setOpaque_(False)
        window.setBackgroundColor_(AppKit.NSColor.clearColor())

        # Make the titlebar transparent so the vibrancy extends all the way up
        window.setTitlebarAppearsTransparent_(True)
        # Optional: extend content under titlebar for a completely seamless look
        # window.setStyleMask_(window.styleMask() | AppKit.NSWindowStyleMaskFullSizeContentView)

        last_error = None
        return True
    except Exception as exc:
        last_error = f"{type(exc).__name__}: {exc}"
        return False

def remove_vibrancy(widget) -> bool:
    if not is_available() or not _is_cocoa():
        return False
    try:
        import objc
        import AppKit
        from PyQt6.QtCore import Qt

        view = objc.objc_object(c_void_p=int(widget.winId()))
        window = view.window()
        if window is None:
            return False

        content = window.contentView()
        superview = content.superview()

        # Remove the effect view if it exists
        for subview in superview.subviews() if superview is not None else content.subviews():
            if isinstance(subview, AppKit.NSVisualEffectView):
                subview.removeFromSuperview()
                break

        # Revert the transparent titlebar and restore standard OS background.
        # Now that WA_TranslucentBackground is no longer forced on the Qt side,
        # we need the OS window to be opaque to support standard titlebar rendering.
        window.setTitlebarAppearsTransparent_(False)
        window.setOpaque_(True)
        window.setBackgroundColor_(AppKit.NSColor.windowBackgroundColor())

        return True
    except Exception:
        return False
