import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from gemini_translator.ui.dialogs.proxy import ProxySettingsDialog


class _Signal:
    def __init__(self):
        self._slots = []

    def connect(self, slot):
        self._slots.append(slot)

    def emit(self, *args):
        for slot in list(self._slots):
            slot(*args)


class _Bus:
    def __init__(self):
        self.event_posted = _Signal()


class _SettingsManager:
    """Минимальный стаб settings_manager по образцу test_proxy_dialog_ssh_mode.py."""

    def __init__(self, settings=None):
        self.settings = dict(settings or {})
        self.bus = _Bus()

    def load_proxy_settings(self):
        result = dict(self.settings)
        result["saved_proxies"] = [dict(item) for item in self.settings.get("saved_proxies", [])]
        return result

    def save_proxy_settings(self, settings):
        self.settings = dict(settings)
        self.bus.event_posted.emit({"event": "proxy_settings_changed", "data": dict(settings)})
        return True


class ProxyDialogSshPortCrashTests(unittest.TestCase):
    """ui-dialogs-other/bugs/1-proxy-ssh-port-crash.

    validate_inputs() проверяет ssh_port_edit только в SSH-режиме, но
    _collect_proxy_settings() безусловно делает int(ssh_port_edit.text() or 22).
    Если пользователь ввёл мусор в SSH-порт в SSH-режиме, а затем вернулся к
    обычному режиму (текст поля не сбрасывается), accept() падает с ValueError.
    """

    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_accept_does_not_crash_on_stale_invalid_ssh_port_after_mode_switch(self):
        manager = _SettingsManager()
        dialog = ProxySettingsDialog(settings_manager=manager)

        # Переключаемся в SSH-режим и вводим невалидный SSH-порт.
        dialog.tunnel_mode_combo.setCurrentText("Автотуннель через SSH")
        dialog.ssh_port_edit.setText("2222abc")

        # Возвращаемся в обычный режим: поле ssh_port_edit не сбрасывается.
        dialog.tunnel_mode_combo.setCurrentText("Обычный прокси")
        self.assertEqual(dialog.ssh_port_edit.text(), "2222abc")

        dialog.proxy_host_edit.setText("127.0.0.1")
        dialog.proxy_port_edit.setText("8080")

        # validate_inputs() не проверяет ssh_port вне SSH-режима -> должен
        # вернуть True, а accept() не должен падать с необработанным ValueError.
        self.assertTrue(dialog.validate_inputs())
        try:
            dialog.accept()
        except ValueError as exc:
            self.fail(f"accept() упал с ValueError на невалидном ssh_port: {exc}")

        self.assertEqual(manager.settings["ssh_port"], 22)

    def test_collect_proxy_settings_falls_back_to_default_ssh_port_on_garbage(self):
        manager = _SettingsManager()
        dialog = ProxySettingsDialog(settings_manager=manager)
        dialog.proxy_host_edit.setText("127.0.0.1")
        dialog.proxy_port_edit.setText("8080")
        dialog.ssh_port_edit.setText("not-a-number")

        settings = dialog._collect_proxy_settings()

        self.assertEqual(settings["ssh_port"], 22)


if __name__ == "__main__":
    unittest.main()
