import pytest
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QSettings
from gemini_translator.ui.notifications import NotificationManager

def test_notification_settings_toggle(monkeypatch):
    """Test that NotificationManager respects QSettings."""
    app = QApplication.instance() or QApplication([])
    settings = QSettings("SiberianTeam", "TranslatorFork")
    
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
