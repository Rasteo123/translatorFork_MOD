"""Нечитаемый файл настроек надо сохранить, а не закатать дефолтами.

Раньше при ошибке разбора загрузка молча возвращала пустой словарь, приложение
поднималось на значениях по умолчанию и первым же автосохранением уничтожало
единственную копию настроек вместе со всеми ключами.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore, QtWidgets

from gemini_translator.utils.settings import SettingsManager


class _RecordingBus(QtCore.QObject):
    event_posted = QtCore.pyqtSignal(dict)

    def __init__(self):
        super().__init__()
        self.events = []
        self.event_posted.connect(self.events.append)


def _qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _quarantined(tmp_path):
    return sorted(p for p in os.listdir(tmp_path) if ".corrupt-" in p)


def test_corrupt_file_is_quarantined_before_defaults_are_written(tmp_path):
    _qapp()
    path = tmp_path / "settings.json"
    damaged = '{"api_keys_with_status": [{"key": "KEY_A"'
    path.write_text(damaged, encoding="utf-8")

    manager = SettingsManager(config_file=str(path))
    manager.save_custom_prompt("дефолт поверх битого файла")

    backups = _quarantined(tmp_path)
    assert len(backups) == 1
    assert (tmp_path / backups[0]).read_text(encoding="utf-8") == damaged


def test_corrupt_file_is_reported_on_the_bus(tmp_path):
    _qapp()
    bus = _RecordingBus()
    path = tmp_path / "settings.json"
    path.write_text("{не json", encoding="utf-8")

    SettingsManager(event_bus=bus, config_file=str(path))

    corrupted = [e for e in bus.events if e["event"] == "settings_file_corrupted"]
    assert len(corrupted) == 1
    assert os.path.exists(corrupted[0]["data"]["backup_path"])


def test_missing_file_is_not_treated_as_corruption(tmp_path):
    _qapp()
    bus = _RecordingBus()

    manager = SettingsManager(event_bus=bus, config_file=str(tmp_path / "settings.json"))
    manager.save_custom_prompt("первый запуск")

    assert _quarantined(tmp_path) == []
    assert [e for e in bus.events if e["event"] == "settings_file_corrupted"] == []


def test_valid_file_is_not_quarantined(tmp_path):
    _qapp()
    path = tmp_path / "settings.json"
    seeded = SettingsManager(config_file=str(path))
    seeded.add_keys_atomically({"KEY_A"}, "gemini")

    reopened = SettingsManager(config_file=str(path))
    reopened.save_custom_prompt("обычная работа")

    assert _quarantined(tmp_path) == []
    assert [i["key"] for i in reopened.load_key_statuses()] == ["KEY_A"]
