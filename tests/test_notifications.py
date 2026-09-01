import builtins
import importlib
import sys

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QSettings
from gemini_translator.ui.notifications import NotificationManager


def test_notifications_import_without_loguru(monkeypatch):
    """Notification module should import even when optional loguru is absent."""
    original_module = sys.modules.get("gemini_translator.ui.notifications")
    parent_module = sys.modules.get("gemini_translator.ui")
    had_parent_attr = parent_module is not None and hasattr(parent_module, "notifications")
    original_parent_attr = getattr(parent_module, "notifications", None) if had_parent_attr else None
    sys.modules.pop("gemini_translator.ui.notifications", None)

    real_import = builtins.__import__

    def import_without_loguru(name, *args, **kwargs):
        if name == "loguru":
            raise ModuleNotFoundError("No module named 'loguru'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_without_loguru)

    try:
        module = importlib.import_module("gemini_translator.ui.notifications")
        assert module.NotificationManager is not None
    finally:
        sys.modules.pop("gemini_translator.ui.notifications", None)
        if original_module is not None:
            sys.modules["gemini_translator.ui.notifications"] = original_module
        if parent_module is not None:
            if had_parent_attr:
                setattr(parent_module, "notifications", original_parent_attr)
            elif hasattr(parent_module, "notifications"):
                delattr(parent_module, "notifications")


def test_notification_settings_toggle(monkeypatch, request):
    """Test that NotificationManager respects QSettings."""
    if QApplication.instance() is None:
        QApplication([])
    settings = QSettings("SiberianTeam", "TranslatorFork")
    # Даже с изоляцией из conftest тест не оставляет за собой чужое значение:
    # ключ общий с живым приложением, а «выключено» — не то, с чем стоит
    # заканчивать прогон.
    previous = settings.value("notifications_enabled", None)

    def _restore():
        if previous is None:
            settings.remove("notifications_enabled")
        else:
            settings.setValue("notifications_enabled", previous)
        settings.sync()
    request.addfinalizer(_restore)
    
    # Mock subprocess.Popen and QSystemTrayIcon to avoid actual OS notifications during tests
    called = []
    
    def mock_popen(*args, **kwargs):
        called.append("subprocess")
        
    class MockTrayIcon:
        class MessageIcon:
            Information = 1
            
        def __init__(self, parent=None):
            pass
        def setIcon(self, icon):
            pass
        def show(self):
            pass
        def isSystemTrayAvailable(self):
            return True
        def showMessage(self, title, message, icon, timeout):
            called.append("tray")
            
    monkeypatch.setattr("gemini_translator.ui.notifications.subprocess.Popen", mock_popen)
    monkeypatch.setattr("gemini_translator.ui.notifications.QSystemTrayIcon", MockTrayIcon)
    
    # Force _tray_icon to None to test initialization
    NotificationManager._tray_icon = None

    # Test enabled
    settings.setValue("notifications_enabled", True)
    NotificationManager.show("Test", "Message")
    assert len(called) > 0, "Notification should have been triggered (tray or subprocess)"
    
    called.clear()
    
    # Test disabled
    settings.setValue("notifications_enabled", False)
    NotificationManager.show("Test", "Message")
    assert len(called) == 0, "Notification should NOT be triggered when disabled"
