import os
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore, QtWidgets

from gemini_translator.api import config as api_config
from gemini_translator.utils.settings import SettingsManager


class _RecordingBus(QtCore.QObject):
    event_posted = QtCore.pyqtSignal(dict)

    def __init__(self):
        super().__init__()
        self.events = []
        self.event_posted.connect(self.events.append)


class SettingsLiveKeyResetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        api_config.initialize_configs()

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

    def _create_manager(self, bus):
        return SettingsManager(
            event_bus=bus,
            config_file=os.path.join(self.temp_dir.name, "settings.json"),
        )

    def test_expired_gemini_key_is_reset_and_announced_without_reload(self):
        bus = _RecordingBus()
        manager = self._create_manager(bus)

        expired_at = int(time.time()) - (48 * 60 * 60)
        with manager.file_lock:
            manager._cache["api_keys_with_status"] = [
                {
                    "key": "GEMINI_TEST_KEY",
                    "provider": "gemini",
                    "status_by_model": {
                        "gemini-test-model": {
                            "exhausted_at": expired_at,
                            "exhausted_level": 2,
                            "requests": [expired_at],
                        }
                    },
                }
            ]

        bus.events.clear()
        manager._refresh_expired_key_limits()

        key_info = manager.get_key_info("GEMINI_TEST_KEY")
        model_status = key_info["status_by_model"]["gemini-test-model"]
        self.assertIsNone(model_status["exhausted_at"])
        self.assertEqual(model_status["exhausted_level"], 0)
        self.assertEqual(manager.get_request_count(key_info, "gemini-test-model"), 0)
        self.assertTrue(
            any(
                event.get("event") == "key_statuses_updated"
                and event.get("data", {}).get("reason") == "automatic_limit_reset"
                for event in bus.events
            )
        )

    def test_limit_maintenance_timer_runs_while_application_is_open(self):
        manager = self._create_manager(_RecordingBus())

        self.assertTrue(manager._limit_maintenance_timer.isActive())
        self.assertEqual(manager._limit_maintenance_timer.interval(), 5000)


if __name__ == "__main__":
    unittest.main()
