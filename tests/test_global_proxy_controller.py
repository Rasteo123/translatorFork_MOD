import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from gemini_translator.utils.proxy_tool import GlobalProxyController


class _Signal:
    def __init__(self):
        self._slots = []

    def connect(self, slot):
        self._slots.append(slot)

    def emit(self, *args):
        for slot in list(self._slots):
            slot(*args)


class _EventBus:
    def __init__(self):
        self.event_posted = _Signal()


class _FakeTunnelManager:
    def __init__(self):
        self.start_calls = []
        self.stop_calls = 0
        self._active = False
        self.status_changed = _Signal()

    @property
    def active(self):
        return self._active

    def start(self, **kwargs):
        self.start_calls.append(kwargs)
        self._active = True

    def stop(self):
        self.stop_calls += 1
        self._active = False


class GlobalProxyControllerTunnelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _settings(self, **overrides):
        settings = {
            "enabled": True,
            "type": "SOCKS5",
            "tunnel_mode": "ssh",
            "host": "127.0.0.1",
            "port": 8080,
            "ssh_host": "91.107.201.91",
            "ssh_port": 22,
            "ssh_user": "root",
            "ssh_key_path": "/tmp/id_ed25519",
        }
        settings.update(overrides)
        return settings

    def _make_controller(self):
        bus = _EventBus()
        tunnel = _FakeTunnelManager()
        received = []
        bus.event_posted.connect(lambda event: received.append(event))
        controller = GlobalProxyController(bus, tunnel_manager=tunnel)
        return controller, bus, tunnel, received

    def test_enabling_ssh_mode_starts_tunnel_with_expected_arguments(self):
        controller, _, tunnel, _ = self._make_controller()

        controller.apply_settings(self._settings())

        self.assertEqual(tunnel.start_calls, [{
            "ssh_host": "91.107.201.91",
            "ssh_port": 22,
            "ssh_user": "root",
            "ssh_key_path": "/tmp/id_ed25519",
            "local_port": 8080,
        }])

    def test_same_settings_do_not_bypass_manager_restart_backoff(self):
        controller, _, tunnel, _ = self._make_controller()
        settings = self._settings()
        controller.apply_settings(settings)
        tunnel._active = False  # Process is down while manager owns a retry timer.

        controller.apply_settings(settings)

        self.assertEqual(len(tunnel.start_calls), 1)

    def test_changed_tunnel_settings_restart_running_tunnel(self):
        changes = {
            "ssh_host": "new.example",
            "ssh_port": 2222,
            "ssh_user": "deploy",
            "ssh_key_path": "/tmp/other_key",
            "port": 9080,
        }

        for field, value in changes.items():
            with self.subTest(field=field):
                controller, _, tunnel, _ = self._make_controller()
                controller.apply_settings(self._settings())

                controller.apply_settings(self._settings(**{field: value}))

                self.assertEqual(tunnel.stop_calls, 1)
                self.assertEqual(len(tunnel.start_calls), 2)
                parameter = "local_port" if field == "port" else field
                self.assertEqual(tunnel.start_calls[-1][parameter], value)

    def test_disabling_stops_tunnel_even_while_it_is_waiting_to_retry(self):
        controller, _, tunnel, _ = self._make_controller()
        controller.apply_settings(self._settings())
        tunnel._active = False

        controller.apply_settings(self._settings(enabled=False))

        self.assertEqual(tunnel.stop_calls, 1)

    def test_non_ssh_mode_stops_tunnel(self):
        controller, _, tunnel, _ = self._make_controller()
        controller.apply_settings(self._settings())
        tunnel._active = False

        controller.apply_settings(self._settings(tunnel_mode="none"))

        self.assertEqual(tunnel.stop_calls, 1)

    def test_disabled_settings_stop_manager_without_controller_owned_config(self):
        controller, _, tunnel, _ = self._make_controller()

        controller.apply_settings(self._settings(enabled=False))

        self.assertEqual(tunnel.stop_calls, 1)

    def test_non_ssh_settings_stop_manager_without_controller_owned_config(self):
        controller, _, tunnel, _ = self._make_controller()

        controller.apply_settings(self._settings(tunnel_mode="none"))

        self.assertEqual(tunnel.stop_calls, 1)

    def test_invalid_tunnel_ports_stop_pending_manager_and_do_not_start(self):
        invalid_ports = (
            ("ssh_port", None),
            ("ssh_port", 0),
            ("ssh_port", 65536),
            ("ssh_port", True),
            ("ssh_port", 22.5),
            ("local_port", None),
            ("local_port", 0),
            ("local_port", 65536),
            ("local_port", False),
            ("local_port", 8080.5),
        )

        for field, value in invalid_ports:
            with self.subTest(field=field, value=value):
                controller, _, tunnel, received = self._make_controller()
                settings_field = "port" if field == "local_port" else field

                controller.apply_settings(self._settings(**{settings_field: value}))

                self.assertEqual(tunnel.start_calls, [])
                self.assertEqual(tunnel.stop_calls, 1)
                status = [
                    event for event in received
                    if event.get("event") == "current_proxy_status"
                ][-1]
                self.assertEqual(status["data"]["tunnel_state"], "error")

    def test_missing_required_ssh_settings_stop_pending_manager(self):
        for field in ("ssh_host", "ssh_user", "ssh_key_path"):
            with self.subTest(field=field):
                controller, _, tunnel, _ = self._make_controller()

                controller.apply_settings(self._settings(**{field: "  "}))

                self.assertEqual(tunnel.start_calls, [])
                self.assertEqual(tunnel.stop_calls, 1)

    def test_proxy_started_event_applies_saved_settings(self):
        _, bus, tunnel, _ = self._make_controller()

        bus.event_posted.emit({"event": "proxy_started", "data": self._settings()})

        self.assertEqual(len(tunnel.start_calls), 1)

    def test_tunnel_status_relay_keeps_current_proxy_settings(self):
        controller, _, tunnel, received = self._make_controller()
        controller.apply_settings(self._settings())

        tunnel.status_changed.emit("error", "Permission denied")

        status = [event for event in received if event.get("event") == "current_proxy_status"][-1]
        expected = self._settings()
        expected.update({
            "tunnel_state": "error",
            "tunnel_message": "Permission denied",
        })
        self.assertEqual(status["data"], expected)

    def test_shutdown_cancels_retry_even_without_active_process(self):
        controller, _, tunnel, _ = self._make_controller()
        controller.apply_settings(self._settings())
        tunnel._active = False

        controller.shutdown()

        self.assertEqual(tunnel.stop_calls, 1)


if __name__ == "__main__":
    unittest.main()
