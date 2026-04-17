"""Centralized window-title branding for translatorFork_MOD."""

APP_WINDOW_BRAND = "translatorFork_MOD"


def rebrand_window_title(title):
    text = "" if title is None else str(title).strip()
    if not text:
        return APP_WINDOW_BRAND
    if text == APP_WINDOW_BRAND or text.startswith(APP_WINDOW_BRAND):
        return text
    return f"{APP_WINDOW_BRAND} - {text}"


def install_window_title_branding(app=None):
    from PyQt6 import QtWidgets

    widget_class = QtWidgets.QWidget
    if not getattr(widget_class, "_translatorfork_mod_title_patch_installed", False):
        original_set_window_title = widget_class.setWindowTitle

        def branded_set_window_title(self, title):
            original_set_window_title(self, rebrand_window_title(title))

        widget_class.setWindowTitle = branded_set_window_title
        widget_class._translatorfork_mod_title_patch_installed = True

    application = app or QtWidgets.QApplication.instance()
    if application is not None:
        application.setApplicationName(APP_WINDOW_BRAND)
        if hasattr(application, "setApplicationDisplayName"):
            application.setApplicationDisplayName(APP_WINDOW_BRAND)
        for widget in application.topLevelWidgets():
            widget.setWindowTitle(widget.windowTitle())
