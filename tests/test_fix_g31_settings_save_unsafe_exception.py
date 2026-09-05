"""
Регресс для utils-io/bugs/4-settings-save-unsafe-exception.

_perform_save (settings.py) ловил только OSError. Любая другая ошибка
сериализации (например TypeError от несериализуемого значения в кэше,
как set) пробрасывалась необработанной из слота таймера/aboutToQuit,
а _is_dirty оставался True навсегда — автосохранение "залипало".
"""
import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore, QtWidgets

from gemini_translator.utils.settings import SettingsManager


class _RecordingBus(QtCore.QObject):
    event_posted = QtCore.pyqtSignal(dict)

    def __init__(self):
        super().__init__()
        self.events = []
        self.event_posted.connect(self.events.append)


class SettingsSaveNonOSErrorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_perform_save_handles_non_serializable_value_without_raising(self):
        """
        _perform_save не должен пробрасывать исключение наружу (в слот таймера),
        когда в кэше оказалось несериализуемое значение (TypeError от json.dumps).
        Вместо этого должна сохраняться диагностика через _last_save_error и
        событие settings_save_failed, как это уже сделано для OSError.
        """
        bus = _RecordingBus()

        with tempfile.TemporaryDirectory() as tmpdir:
            manager = SettingsManager(
                event_bus=bus,
                config_file=os.path.join(tmpdir, "settings.json"),
            )
            # Несериализуемое значение (set) попало в кэш каким-то путём.
            manager._cache = {"custom_prompt": "changed", "broken": {1, 2, 3}}
            manager._is_dirty = True

            # Раньше здесь вылетал необработанный TypeError прямо из слота.
            manager._perform_save()

        self.assertTrue(manager._is_dirty)
        self.assertIsInstance(manager._last_save_error, TypeError)
        self.assertTrue(bus.events, "ожидалось событие settings_save_failed на шине")
        self.assertEqual(bus.events[-1]["event"], "settings_save_failed")


if __name__ == "__main__":
    unittest.main()
