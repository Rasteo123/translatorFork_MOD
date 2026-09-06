import sys
import subprocess
import logging
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication, QSystemTrayIcon, QStyle
from PyQt6.QtGui import QIcon

logger = logging.getLogger(__name__)

class NotificationManager:
    _tray_icon = None
    _SETTINGS_ORG = "SiberianTeam"
    _SETTINGS_APP = "TranslatorFork"
    _SETTINGS_KEY = "notifications_enabled"

    @classmethod
    def is_enabled(cls) -> bool:
        """Единая точка чтения флага notifications_enabled из QSettings."""
        settings = QSettings(cls._SETTINGS_ORG, cls._SETTINGS_APP)
        return bool(settings.value(cls._SETTINGS_KEY, True, type=bool))

    @classmethod
    def set_enabled(cls, enabled: bool) -> None:
        """Единая точка записи флага notifications_enabled в QSettings.

        Сигнатура (bool) позволяет подключать её напрямую к QCheckBox.toggled
        без приватного слота-обёртки в каждом диалоге.
        """
        settings = QSettings(cls._SETTINGS_ORG, cls._SETTINGS_APP)
        settings.setValue(cls._SETTINGS_KEY, bool(enabled))

    @classmethod
    def show(cls, title: str, message: str):
        if not cls.is_enabled():
            return

        if sys.platform == 'darwin':
            # On macOS, we use QSystemTrayIcon to trigger native UNUserNotificationCenter
            # We keep a global reference to avoid garbage collection
            if cls._tray_icon is None:
                app = QApplication.instance()
                if app is not None:
                    cls._tray_icon = QSystemTrayIcon(app)
                    icon_path = "gemini_translator/GT.ico"
                    # Fallback to standard icon if GT.ico is missing
                    try:
                        icon = QIcon(icon_path)
                        if icon.isNull():
                            icon = app.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
                    except Exception:
                        icon = app.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
                    cls._tray_icon.setIcon(icon)
                    cls._tray_icon.show()
            
            if cls._tray_icon and cls._tray_icon.isSystemTrayAvailable():
                cls._tray_icon.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, 5000)
                try:
                    subprocess.Popen(['osascript', '-e', 'beep'])
                except Exception:
                    pass
            else:
                # Fallback to osascript if UI is not fully initialized or tray fails
                safe_msg = str(message).replace('"', '\\"')
                safe_title = str(title).replace('"', '\\"')
                script = f'display notification "{safe_msg}" with title "{safe_title}" sound name "default"'
                try:
                    subprocess.Popen(['osascript', '-e', script])
                except Exception as e:
                    logger.error(f"Failed to send macOS notification: {e}")

        elif sys.platform == 'win32':
            safe_msg = str(message).replace("'", "''").replace('<', '&lt;').replace('>', '&gt;')
            safe_title = str(title).replace("'", "''").replace('<', '&lt;').replace('>', '&gt;')
            ps_script = f"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$xml = "<toast><visual><binding template='ToastText02'><text id='1'>{safe_title}</text><text id='2'>{safe_msg}</text></binding></visual></toast>"
$doc = [Windows.Data.Xml.Dom.XmlDocument]::new()
$doc.LoadXml($xml)
$toast = [Windows.UI.Notifications.ToastNotification]::new($doc)
$notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("SiberianTeam.GeminiTranslator")
$notifier.Show($toast)
"""
            try:
                subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script], creationflags=0x08000000)
            except Exception as e:
                logger.error(f"Failed to send Windows notification: {e}")
