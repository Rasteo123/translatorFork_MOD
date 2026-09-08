import json
import os
import sqlite3
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore, QtWidgets

from gemini_translator.api import config as api_config
from gemini_translator.utils.settings import SettingsManager


@pytest.fixture(scope="module", autouse=True)
def qt_application():
    api_config.initialize_configs()
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class RecordingBus(QtCore.QObject):
    event_posted = QtCore.pyqtSignal(dict)

    def __init__(self):
        super().__init__()
        self.events = []
        self.event_posted.connect(self.events.append)


def test_plain_settings_do_not_create_runtime_database(tmp_path):
    manager = SettingsManager(config_file=str(tmp_path / "settings.json"))
    manager.save_custom_prompt("hello")
    manager.load_settings()
    assert not (tmp_path / "settings.runtime.sqlite3").exists()


def test_public_loaders_materialize_same_status_shape(tmp_path):
    manager = SettingsManager(config_file=str(tmp_path / "settings.json"))
    manager.save_key_statuses([{
        "key": "KEY",
        "provider": "gemini",
        "status_by_model": {
            "model": {"exhausted_at": 10.0, "exhausted_level": 2, "requests": [1, 2]}
        },
    }])

    expected = {"exhausted_at": 10.0, "exhausted_level": 2, "requests": [1, 2]}
    assert manager.load_key_statuses()[0]["status_by_model"]["model"] == expected
    assert manager.get_key_info("KEY")["status_by_model"]["model"] == expected
    assert manager.load_settings()["api_keys_with_status"][0]["status_by_model"]["model"] == expected
    on_disk = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert on_disk["api_keys_with_status"] == [{"key": "KEY", "provider": "gemini"}]


def test_legacy_statuses_remain_visible_before_runtime_migration(tmp_path):
    settings_path = tmp_path / "settings.json"
    now = int(time.time())
    expected = {"exhausted_at": now, "exhausted_level": 2, "requests": [now, now]}
    settings_path.write_text(json.dumps({"api_keys_with_status": [{
        "key": "KEY", "provider": "gemini", "status_by_model": {"model": expected}
    }]}), encoding="utf-8")
    manager = SettingsManager(config_file=str(settings_path))

    assert manager.load_key_statuses()[0]["status_by_model"]["model"] == expected
    assert manager.get_key_info("KEY")["status_by_model"]["model"] == expected
    assert manager.load_settings()["api_keys_with_status"][0]["status_by_model"]["model"] == expected


def test_sqlite_overrides_matching_legacy_model_and_keeps_other_models(tmp_path):
    settings_path = tmp_path / "settings.json"
    legacy = {"exhausted_at": None, "exhausted_level": 0, "requests": [1]}
    settings_path.write_text(json.dumps({"api_keys_with_status": [{
        "key": "KEY", "provider": "gemini",
        "status_by_model": {"shared": legacy, "legacy-only": legacy},
    }]}), encoding="utf-8")
    manager = SettingsManager(config_file=str(settings_path))
    stored = {"exhausted_at": 20.0, "exhausted_level": 1, "requests": [3]}
    manager._key_runtime_store.merge_statuses({"KEY": {"shared": stored}})

    assert manager.get_key_info("KEY")["status_by_model"] == {
        "shared": stored, "legacy-only": legacy,
    }
    assert manager._cache["api_keys_with_status"][0]["status_by_model"]["shared"] == legacy


@pytest.mark.parametrize("loader", ["load_key_statuses", "get_key_info", "load_settings"])
def test_public_results_are_independent_deep_copies(tmp_path, loader):
    manager = SettingsManager(config_file=str(tmp_path / "settings.json"))
    manager.save_key_statuses([{
        "key": "KEY", "provider": "gemini", "metadata": {"labels": ["original"]},
        "status_by_model": {"model": {"requests": [1], "exhausted_level": 0}},
    }])
    if loader == "get_key_info":
        result = manager.get_key_info("KEY")
    elif loader == "load_settings":
        result = manager.load_settings()["api_keys_with_status"][0]
    else:
        result = manager.load_key_statuses()[0]
    result["metadata"]["labels"].append("changed")
    result["status_by_model"]["model"]["requests"].append(2)

    reloaded = manager.get_key_info("KEY")
    assert reloaded["metadata"]["labels"] == ["original"]
    assert reloaded["status_by_model"]["model"]["requests"] == [1]


def test_store_operational_error_is_recorded_reported_and_propagated(tmp_path, monkeypatch):
    bus = RecordingBus()
    manager = SettingsManager(event_bus=bus, config_file=str(tmp_path / "settings.json"))
    manager.add_keys_atomically({"KEY"}, "gemini")
    failure = sqlite3.OperationalError("database is locked")
    original_load = manager._key_runtime_store.load_statuses

    def fail_load(_keys):
        raise failure

    monkeypatch.setattr(manager._key_runtime_store, "load_statuses", fail_load)
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        manager.load_key_statuses()

    assert manager._last_runtime_store_error is failure
    assert bus.events[-1] == {
        "event": "key_runtime_store_failed", "source": "SettingsManager",
        "data": {"operation": "load_key_statuses", "message": "database is locked",
                 "filename": str(tmp_path / "settings.runtime.sqlite3")},
    }
    monkeypatch.setattr(manager._key_runtime_store, "load_statuses", original_load)
    manager.load_key_statuses()
    assert manager._last_runtime_store_error is None


def test_runtime_save_failure_preserves_config_write_without_success_event(tmp_path, monkeypatch):
    bus = RecordingBus()
    settings_path = tmp_path / "settings.json"
    manager = SettingsManager(event_bus=bus, config_file=str(settings_path))
    failure = sqlite3.OperationalError("database is locked")

    def fail_merge(_statuses):
        raise failure

    monkeypatch.setattr(manager._key_runtime_store, "merge_statuses", fail_merge)
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        manager.save_key_statuses([{
            "key": "KEY", "provider": "gemini", "requests": [1],
            "exhausted_at": 2, "exhausted_level": 2,
            "status_by_model": {"model": {"requests": [1]}},
        }])

    assert json.loads(settings_path.read_text(encoding="utf-8"))["api_keys_with_status"] == [
        {"key": "KEY", "provider": "gemini"},
    ]
    assert manager._last_runtime_store_error is failure
    assert [item["event"] for item in bus.events] == ["key_runtime_store_failed"]
    assert bus.events[0]["data"]["operation"] == "save_key_statuses"


def test_corrupt_store_recovery_is_published(tmp_path):
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({
        "api_keys_with_status": [{"key": "KEY", "provider": "gemini"}]
    }), encoding="utf-8")
    runtime_path = tmp_path / "settings.runtime.sqlite3"
    runtime_path.write_bytes(b"not sqlite")
    bus = RecordingBus()

    manager = SettingsManager(event_bus=bus, config_file=str(settings_path))
    assert manager.load_key_statuses()[0]["status_by_model"] == {}
    event = next(item for item in bus.events if item["event"] == "key_runtime_store_corrupted")
    assert event["data"]["database_file"] == str(runtime_path)
    assert Path(event["data"]["backup_path"]).read_bytes() == b"not sqlite"
