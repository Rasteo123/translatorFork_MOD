import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from gemini_translator.ui.dialogs.proxy import ProxySettingsDialog


class _LineEditStub:
    def __init__(self, text=""):
        self._text = text

    def text(self):
        return self._text


class _ComboStub:
    def __init__(self, value="Обычный прокси"):
        self._value = value

    def currentText(self):
        return self._value


class _ValidationHarness:
    validate_inputs = ProxySettingsDialog.validate_inputs
    _is_ssh_mode = ProxySettingsDialog._is_ssh_mode

    def __init__(
        self,
        *,
        tunnel_mode="Обычный прокси",
        port="8080",
        host="127.0.0.1",
        ssh_host="91.107.201.91",
        ssh_port="22",
        ssh_user="root",
        ssh_key_path="/tmp/does-not-exist",
    ):
        self.tunnel_mode_combo = _ComboStub(tunnel_mode)
        self.proxy_port_edit = _LineEditStub(port)
        self.proxy_host_edit = _LineEditStub(host)
        self.ssh_host_edit = _LineEditStub(ssh_host)
        self.ssh_port_edit = _LineEditStub(ssh_port)
        self.ssh_user_edit = _LineEditStub(ssh_user)
        self.ssh_key_path_edit = _LineEditStub(ssh_key_path)


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


class ProxyDialogSshModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _validate(self, **kwargs):
        harness = _ValidationHarness(**kwargs)
        with mock.patch(
            "gemini_translator.ui.dialogs.proxy.QMessageBox.warning"
        ) as warning:
            result = harness.validate_inputs()
        return result, warning

    def test_normal_proxy_mode_ignores_ssh_fields(self):
        result, warning = self._validate(ssh_host="", ssh_user="", ssh_key_path="")

        self.assertTrue(result)
        warning.assert_not_called()

    def test_ssh_mode_requires_host_user_and_existing_key(self):
        cases = [
            {"ssh_host": ""},
            {"ssh_user": ""},
            {"ssh_key_path": "/tmp/does-not-exist-xyz"},
        ]
        for values in cases:
            with self.subTest(values=values):
                result, warning = self._validate(
                    tunnel_mode="Автотуннель через SSH", **values
                )
                self.assertFalse(result)
                warning.assert_called_once()

    def test_ssh_mode_validates_ssh_port(self):
        for port in ("invalid", "0", "65536"):
            with self.subTest(port=port):
                result, warning = self._validate(
                    tunnel_mode="Автотуннель через SSH", ssh_port=port
                )
                self.assertFalse(result)
                warning.assert_called_once()

    def test_ssh_mode_accepts_existing_key(self):
        with tempfile.NamedTemporaryFile() as key:
            result, warning = self._validate(
                tunnel_mode="Автотуннель через SSH", ssh_key_path=key.name
            )

        self.assertTrue(result)
        warning.assert_not_called()

    def test_accept_persists_ssh_fields_and_forces_local_socks5(self):
        manager = _SettingsManager()
        dialog = ProxySettingsDialog(settings_manager=manager)
        with tempfile.NamedTemporaryFile() as key:
            dialog.tunnel_mode_combo.setCurrentText("Автотуннель через SSH")
            dialog.ssh_host_edit.setText("proxy.example")
            dialog.ssh_port_edit.setText("2222")
            dialog.ssh_user_edit.setText("alice")
            dialog.ssh_key_path_edit.setText(key.name)
            dialog.proxy_port_edit.setText("1080")
            dialog.proxy_enabled_checkbox.setChecked(True)

            dialog.accept()

        self.assertEqual(manager.settings["tunnel_mode"], "ssh")
        self.assertEqual(manager.settings["type"], "SOCKS5")
        self.assertEqual(manager.settings["host"], "127.0.0.1")
        self.assertEqual(manager.settings["port"], 1080)
        self.assertEqual(manager.settings["ssh_host"], "proxy.example")
        self.assertEqual(manager.settings["ssh_port"], 2222)
        self.assertEqual(manager.settings["ssh_user"], "alice")

    def test_saved_ssh_proxies_with_same_local_port_are_not_duplicates(self):
        manager = _SettingsManager()
        dialog = ProxySettingsDialog(settings_manager=manager)
        with tempfile.NamedTemporaryFile() as key:
            dialog.tunnel_mode_combo.setCurrentText("Автотуннель через SSH")
            dialog.ssh_user_edit.setText("alice")
            dialog.ssh_key_path_edit.setText(key.name)
            dialog.proxy_port_edit.setText("1080")

            dialog.ssh_host_edit.setText("one.example")
            dialog.accept()
            dialog.ssh_host_edit.setText("two.example")
            dialog.accept()

        self.assertEqual(len(manager.settings["saved_proxies"]), 2)

    def test_live_tunnel_status_is_rendered(self):
        manager = _SettingsManager()
        dialog = ProxySettingsDialog(settings_manager=manager)

        manager.bus.event_posted.emit({
            "event": "current_proxy_status",
            "data": {"tunnel_state": "error", "tunnel_message": "Permission denied"},
        })

        self.assertIn("ошибка", dialog.tunnel_status_label.text().lower())
        self.assertIn("Permission denied", dialog.tunnel_status_label.text())


if __name__ == "__main__":
    unittest.main()
