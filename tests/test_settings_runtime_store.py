import json
import os
import sqlite3
import threading
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore, QtWidgets

from gemini_translator.api import config as api_config
from gemini_translator.utils.key_runtime_store import KeyRuntimeStore
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


def _seed_key_runtime(manager, key, statuses_by_model, provider="gemini", **config):
    """Кладёт runtime ключа прямо в SQLite.

    save_key_statuses() пишет только конфигурацию: runtime из принесённого
    снимка он игнорирует намеренно, чтобы не откатывать блокировку, выставленную
    уже после чтения снимка. Значит и в тестах состояние надо готовить там, где
    оно живёт, — в хранилище.
    """
    manager.save_key_statuses([{"key": key, "provider": provider, **config}])
    manager._key_runtime_store.merge_statuses({key: statuses_by_model})


def test_plain_settings_do_not_create_runtime_database(tmp_path):
    manager = SettingsManager(config_file=str(tmp_path / "settings.json"))
    manager.save_custom_prompt("hello")
    manager.load_settings()
    assert not (tmp_path / "settings.runtime.sqlite3").exists()


def test_public_loaders_materialize_same_status_shape(tmp_path):
    manager = SettingsManager(config_file=str(tmp_path / "settings.json"))
    now = int(time.time())
    _seed_key_runtime(manager, "KEY", {
        "model": {"exhausted_at": now, "exhausted_level": 2, "requests": [now, now]}
    })

    expected = {"exhausted_at": now, "exhausted_level": 2, "requests": [now, now]}
    assert manager.load_key_statuses()[0]["status_by_model"]["model"] == expected
    assert manager.get_key_info("KEY")["status_by_model"]["model"] == expected
    assert manager.load_settings()["api_keys_with_status"][0]["status_by_model"]["model"] == expected
    on_disk = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert on_disk["api_keys_with_status"] == [{"key": "KEY", "provider": "gemini"}]


def test_legacy_statuses_remain_visible_after_runtime_migration(tmp_path):
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


def test_sqlite_merge_preserves_migrated_requests_and_other_models(tmp_path):
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
        "shared": {**stored, "requests": [1, 3]}, "legacy-only": legacy,
    }
    assert manager._cache["api_keys_with_status"] == [{"key": "KEY", "provider": "gemini"}]


@pytest.mark.parametrize("loader", ["load_key_statuses", "get_key_info", "load_settings"])
def test_public_results_are_independent_deep_copies(tmp_path, loader):
    manager = SettingsManager(config_file=str(tmp_path / "settings.json"))
    now = int(time.time())
    _seed_key_runtime(
        manager, "KEY", {"model": {"requests": [now], "exhausted_level": 0}},
        metadata={"labels": ["original"]},
    )
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
    assert reloaded["status_by_model"]["model"]["requests"] == [now]


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


def test_runtime_removal_failure_preserves_config_write_without_success_event(tmp_path, monkeypatch):
    """Конфигурация записана даже когда чистка runtime удалённого ключа упала."""
    bus = RecordingBus()
    settings_path = tmp_path / "settings.json"
    manager = SettingsManager(event_bus=bus, config_file=str(settings_path))
    manager.save_key_statuses([{"key": "KEY", "provider": "gemini"}])
    failure = sqlite3.OperationalError("database is locked")

    def fail_delete(_keys):
        raise failure

    monkeypatch.setattr(manager._key_runtime_store, "delete_keys", fail_delete)
    bus.events.clear()
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        manager.save_key_statuses([])

    assert json.loads(settings_path.read_text(encoding="utf-8"))["api_keys_with_status"] == []
    assert manager._last_runtime_store_error is failure
    assert [item["event"] for item in bus.events] == ["key_runtime_store_failed"]
    assert bus.events[0]["data"]["operation"] == "delete_removed_keys"


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


def _legacy_payload():
    return {"api_keys_with_status": [{
        "key": "KEY", "provider": "gemini",
        "status_by_model": {"model": {
            "exhausted_at": 50, "exhausted_level": 2, "requests": [10, 10],
        }},
    }]}


def test_legacy_json_is_imported_once_and_cleaned(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(_legacy_payload()), encoding="utf-8")
    first = SettingsManager(config_file=str(path))
    assert first.get_key_info("KEY")["status_by_model"]["model"]["requests"] == [10, 10]
    assert json.loads(path.read_text(encoding="utf-8"))["api_keys_with_status"] == [
        {"key": "KEY", "provider": "gemini"},
    ]
    second = SettingsManager(config_file=str(path))
    assert second.get_key_info("KEY")["status_by_model"]["model"]["requests"] == [10, 10]


def test_committed_import_is_not_repeated_when_json_cleanup_failed(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    legacy = _legacy_payload()
    path.write_text(json.dumps(legacy), encoding="utf-8")
    real_save = SettingsManager._save_unsafe
    bus = RecordingBus()

    def fail_cleanup(self, data):
        raise OSError("disk full")

    monkeypatch.setattr(SettingsManager, "_save_unsafe", fail_cleanup)
    first = SettingsManager(event_bus=bus, config_file=str(path))
    assert isinstance(first._last_save_error, OSError)
    assert json.loads(path.read_text(encoding="utf-8")) == legacy
    assert first.get_key_info("KEY")["status_by_model"]["model"]["requests"] == [10, 10]
    assert first._cache["api_keys_with_status"] == [{"key": "KEY", "provider": "gemini"}]
    assert any(item["event"] == "settings_save_failed" for item in bus.events)
    monkeypatch.setattr(SettingsManager, "_save_unsafe", real_save)
    second = SettingsManager(config_file=str(path))
    assert second.get_key_info("KEY")["status_by_model"]["model"]["requests"] == [10, 10]
    assert json.loads(path.read_text(encoding="utf-8"))["api_keys_with_status"] == [
        {"key": "KEY", "provider": "gemini"},
    ]


def test_failed_legacy_import_leaves_json_unchanged_and_reports_error(tmp_path):
    path = tmp_path / "settings.json"
    payload = json.dumps(_legacy_payload())
    path.write_text(payload, encoding="utf-8")
    store = KeyRuntimeStore(tmp_path / "settings.runtime.sqlite3")
    store.ensure_ready()
    with sqlite3.connect(store.path) as connection:
        connection.execute("""CREATE TRIGGER reject_import BEFORE INSERT ON key_requests
            BEGIN SELECT RAISE(ABORT, 'import denied'); END""")
    bus = RecordingBus()
    # Сбой импорта сообщается событием, но не мешает приложению запуститься:
    # раньше исключение летело наружу из конструктора.
    SettingsManager(event_bus=bus, config_file=str(path))
    assert path.read_text(encoding="utf-8") == payload
    assert bus.events[-1]["event"] == "key_runtime_store_failed"
    assert store.load_statuses(["KEY"]) == {"KEY": {}}


def test_top_level_legacy_runtime_is_normalized_before_import(tmp_path):
    path = tmp_path / "settings.json"
    model = next(iter(api_config.api_providers_view()["gemini"]["models"].values()))["id"]
    path.write_text(json.dumps({"api_keys_with_status": [{
        "key": "KEY", "provider": "gemini", "requests": [10, 10],
        "exhausted_at": 50, "exhausted_level": 2,
    }]}), encoding="utf-8")
    manager = SettingsManager(config_file=str(path))
    assert manager.get_key_info("KEY")["status_by_model"][model] == {
        "exhausted_at": 50, "exhausted_level": 2, "requests": [10, 10],
    }
    assert json.loads(path.read_text(encoding="utf-8"))["api_keys_with_status"] == [
        {"key": "KEY", "provider": "gemini"},
    ]


def test_save_settings_keeps_config_and_removal_deletes_sidecar_state(tmp_path):
    path = tmp_path / "settings.json"
    manager = SettingsManager(config_file=str(path))
    now = int(time.time())
    runtime = {"exhausted_at": None, "exhausted_level": 0, "requests": [now]}
    _seed_key_runtime(manager, "KEY", {"model": runtime})
    payload = {"custom_prompt": "keep me", "api_keys_with_status": [
        {"key": "KEY", "provider": "gemini"},
    ]}
    assert manager.save_settings(payload) is True
    loaded = manager.load_settings()
    assert loaded["custom_prompt"] == "keep me"
    assert loaded["api_keys_with_status"][0]["status_by_model"] == {"model": runtime}
    assert json.loads(path.read_text(encoding="utf-8"))["api_keys_with_status"] == [
        {"key": "KEY", "provider": "gemini"},
    ]
    payload["api_keys_with_status"][0]["provider"] = "changed"
    assert manager.get_key_info("KEY")["provider"] == "gemini"
    assert manager.remove_keys_atomically({"KEY"}) == 1
    assert manager.get_api_keys() == []
    assert manager._key_runtime_store.load_statuses(["KEY"]) == {"KEY": {}}


def test_next_start_removes_orphan_left_by_failed_delete(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    bus = RecordingBus()
    first = SettingsManager(event_bus=bus, config_file=str(path))
    _seed_key_runtime(first, "KEY", {"model": {"requests": [int(time.time())]}})

    def fail_delete(_keys):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(first._key_runtime_store, "delete_keys", fail_delete)
    bus.events.clear()
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        first.remove_keys_atomically({"KEY"})
    assert first.get_api_keys() == []
    assert first._key_runtime_store.load_statuses(["KEY"])["KEY"]
    assert [event["event"] for event in bus.events] == ["key_runtime_store_failed"]
    second = SettingsManager(config_file=str(path))
    assert second.get_api_keys() == []
    assert second._key_runtime_store.load_statuses(["KEY"]) == {"KEY": {}}


def test_adding_configured_keys_does_not_create_runtime_database(tmp_path):
    path = tmp_path / "settings.json"
    first = SettingsManager(config_file=str(path))
    first.add_keys_atomically({"KEY"}, "gemini")
    second = SettingsManager(config_file=str(path))
    assert second.get_api_keys() == ["KEY"]
    assert not (tmp_path / "settings.runtime.sqlite3").exists()
    assert json.loads(path.read_text(encoding="utf-8"))["api_keys_with_status"] == [
        {"key": "KEY", "provider": "gemini"},
    ]


@pytest.mark.parametrize("save_method", ["save_key_statuses", "save_settings"])
def test_stale_bulk_save_keeps_concurrent_duplicate_requests(tmp_path, save_method):
    path = tmp_path / "settings.json"
    first = SettingsManager(config_file=str(path))
    first.add_keys_atomically({"KEY"}, "gemini")
    first.increment_request_count("KEY", "model")
    snapshot = first.load_settings()
    second = SettingsManager(config_file=str(path))
    second.increment_request_count("KEY", "model")
    second.increment_request_count("KEY", "model")
    value = snapshot if save_method == "save_settings" else snapshot["api_keys_with_status"]
    assert getattr(first, save_method)(value) is True
    assert first.get_request_count(first.get_key_info("KEY"), "model") == 3


def test_two_managers_increment_without_lost_updates(tmp_path):
    path = tmp_path / "settings.json"
    first = SettingsManager(config_file=str(path))
    first.add_keys_atomically({"KEY"}, "gemini")
    second = SettingsManager(config_file=str(path))
    barrier = threading.Barrier(3)
    errors = []

    def increment_many(manager):
        try:
            barrier.wait()
            for _ in range(30):
                assert manager.increment_request_count("KEY", "model") is True
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=increment_many, args=(manager,)) for manager in (first, second)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=10)
    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    assert first.get_request_count(first.get_key_info("KEY"), "model") == 60


def test_exhaustion_event_is_emitted_after_store_commit(tmp_path):
    bus = RecordingBus()
    manager = SettingsManager(event_bus=bus, config_file=str(tmp_path / "settings.json"))
    manager.add_keys_atomically({"KEY"}, "gemini")
    observed = []

    def observe(event):
        if event["event"] == "key_statuses_updated":
            state = KeyRuntimeStore(manager._key_runtime_store.path).load_statuses(["KEY"])
            observed.append(state["KEY"]["model"].exhausted_level if "model" in state["KEY"] else None)

    bus.event_posted.connect(observe)
    assert manager.mark_key_as_exhausted("KEY", "model") is True
    assert observed == [2]
    assert manager.clear_key_exhaustion_status("KEY", "model") is True
    assert observed == [2, 0]


def test_runtime_mutations_do_not_schedule_or_write_json(tmp_path):
    path = tmp_path / "settings.json"
    bus = RecordingBus()
    manager = SettingsManager(event_bus=bus, config_file=str(path))
    manager.add_keys_atomically({"KEY"}, "gemini")
    before = path.read_bytes()
    before_mtime = path.stat().st_mtime_ns
    observed = []

    def observe(event):
        if event["event"] == "request_count_updated":
            stored = KeyRuntimeStore(manager._key_runtime_store.path).load_statuses(["KEY"])
            state = stored["KEY"].get("model")
            observed.append((event["data"]["count"], len(state.requests) if state else None))

    bus.event_posted.connect(observe)
    assert manager.increment_request_count("KEY", "model") is True
    assert manager.decrement_request_count("KEY", "model") is True
    assert manager.decrement_request_count("KEY", "model") is False
    assert observed == [(1, 1), (0, 0)]
    assert manager.mark_key_as_exhausted("KEY", "model") is True
    assert manager.clear_key_exhaustion_status("KEY", "model") is True
    assert manager.clear_key_exhaustion_status("KEY", "model") is False
    assert not manager._save_timer.isActive()
    assert not manager._is_dirty
    assert path.read_bytes() == before
    assert path.stat().st_mtime_ns == before_mtime


@pytest.mark.parametrize("policy,now,cutoff", [
    ({"type": "rolling", "duration_hours": 2}, 100000, 92800),
    ({"type": "rolling"}, 100000, 13600),
    ({"type": "daily", "timezone": "UTC"}, 172800, 86460),
    ({"type": "daily", "timezone": "UTC"}, 172860, 172860),
    ({"type": "daily", "timezone": "Asia/Yekaterinburg", "reset_hour": 5,
      "reset_minute": 0}, 172800, 172800),
    ({"type": "daily", "timezone": "Unknown/TestZone"}, 100000, 13600),
    ({"type": "daily", "timezone": "UTC", "reset_hour": 25}, 100000, 13600),
    ({"type": "other"}, 100000, 13600),
])
def test_request_cutoff_and_filter_keep_exclusive_boundary(tmp_path, policy, now, cutoff):
    manager = SettingsManager(config_file=str(tmp_path / "settings.json"))
    assert manager._request_window_cutoff(policy, now) == cutoff
    assert manager._filter_request_timestamps_in_window(
        [cutoff - 1, cutoff, cutoff + 1, cutoff + 1, "invalid"], policy, now_ts=now,
    ) == [cutoff + 1, cutoff + 1]


def test_maintenance_preserves_newer_concurrent_exhaustion_without_json_write(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    bus = RecordingBus()
    first = SettingsManager(event_bus=bus, config_file=str(path))
    now = int(time.time())
    expired = now - 48 * 3600
    _seed_key_runtime(first, "KEY", {
        "model": {"exhausted_at": expired, "exhausted_level": 2, "requests": [expired, now]},
    })
    second = SettingsManager(config_file=str(path))
    real_maintain = first._key_runtime_store.maintain_model

    def interleave(key, model, cutoff, *, clear_exhausted_at=None):
        second.mark_key_as_exhausted(key, model)
        return real_maintain(key, model, cutoff, clear_exhausted_at=clear_exhausted_at)

    monkeypatch.setattr(first._key_runtime_store, "maintain_model", interleave)
    before = path.stat().st_mtime_ns
    bus.events.clear()
    first._refresh_expired_key_limits()
    state = first.get_key_info("KEY")["status_by_model"]["model"]
    assert state["exhausted_at"] >= now
    assert state["exhausted_level"] == 2
    assert state["requests"] == [now]
    assert path.stat().st_mtime_ns == before
    assert not first._save_timer.isActive()
    assert bus.events[-1]["data"] == {"reason": "automatic_limit_reset"}


@pytest.mark.parametrize("method,store_method", [
    ("increment_request_count", "increment"),
    ("decrement_request_count", "decrement"),
    ("mark_key_as_exhausted", "set_exhausted"),
    ("clear_key_exhaustion_status", "clear_exhaustion"),
    ("_refresh_expired_key_limits", "maintain_model"),
])
def test_runtime_mutation_failure_is_reported_without_success_event(
    tmp_path, monkeypatch, method, store_method,
):
    path = tmp_path / "settings.json"
    bus = RecordingBus()
    manager = SettingsManager(event_bus=bus, config_file=str(path))
    now = int(time.time())
    _seed_key_runtime(manager, "KEY", {
        "model": {"exhausted_at": now, "exhausted_level": 2, "requests": [now]},
    })
    before = path.read_bytes()
    bus.events.clear()
    error = sqlite3.OperationalError("database is locked")

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(manager._key_runtime_store, store_method, fail)
    args = () if method == "_refresh_expired_key_limits" else ("KEY", "model")
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        getattr(manager, method)(*args)
    assert manager._last_runtime_store_error is error
    assert [event["event"] for event in bus.events] == ["key_runtime_store_failed"]
    assert path.read_bytes() == before
    assert not manager._save_timer.isActive()


def test_startup_orphan_cleanup_failure_is_reported_without_blocking_startup(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"api_keys_with_status": []}), encoding="utf-8")
    store = KeyRuntimeStore(tmp_path / "settings.runtime.sqlite3")
    store.increment("ORPHAN", "model", 10, 0)
    with sqlite3.connect(store.path) as connection:
        connection.execute("""CREATE TRIGGER reject_orphan_delete BEFORE DELETE ON key_requests
            BEGIN SELECT RAISE(ABORT, 'cleanup failed'); END""")
    bus = RecordingBus()
    SettingsManager(event_bus=bus, config_file=str(path))
    assert bus.events[-1]["event"] == "key_runtime_store_failed"
    assert store.load_statuses(["ORPHAN"])["ORPHAN"]["model"].requests == (10,)


def test_failed_legacy_import_does_not_reencode_original_json(tmp_path):
    path = tmp_path / "settings.json"
    legacy = {**_legacy_payload(), "custom_prompt": "Текст в старой кодировке"}
    original = json.dumps(legacy, ensure_ascii=False).encode("cp1251")
    path.write_bytes(original)
    store = KeyRuntimeStore(tmp_path / "settings.runtime.sqlite3")
    store.ensure_ready()
    with sqlite3.connect(store.path) as connection:
        connection.execute("""CREATE TRIGGER reject_import BEFORE INSERT ON key_requests
            BEGIN SELECT RAISE(ABORT, 'import denied'); END""")
    SettingsManager(config_file=str(path))
    assert path.read_bytes() == original


def test_automatic_rolling_reset_preserves_subsecond_exhaustion_boundary(tmp_path, monkeypatch):
    manager = SettingsManager(config_file=str(tmp_path / "settings.json"))
    _seed_key_runtime(
        manager, "KEY",
        {"model": {"exhausted_at": 100000.5, "exhausted_level": 2, "requests": []}},
        provider="rolling-test",
    )
    monkeypatch.setattr(api_config, "api_providers_view", lambda: {
        "rolling-test": {"reset_policy": {"type": "rolling", "duration_hours": 1}},
    })
    monkeypatch.setattr("gemini_translator.utils.settings.time.time", lambda: 103600.75)
    manager._refresh_expired_key_limits()
    assert manager.get_key_info("KEY")["status_by_model"]["model"]["exhausted_at"] is None


@pytest.mark.parametrize("runtime_field,value", [
    ("requests", [10, 10]), ("exhausted_at", 50), ("exhausted_level", 2),
])
def test_unmappable_top_level_legacy_stays_in_json_and_lets_the_app_start(
    tmp_path, runtime_field, value,
):
    """Ключ провайдера, которого нет в реестре, не мигрирует и не роняет запуск.

    Его legacy-поле остаётся в JSON — это единственная копия состояния, — а
    остальные ключи переносятся в SQLite как обычно.
    """
    path = tmp_path / "settings.json"
    legacy = _legacy_payload()
    legacy["api_keys_with_status"].append({
        "key": "UNMAPPABLE_SECRET_KEY", "provider": "unknown-provider", runtime_field: value,
    })
    path.write_bytes(json.dumps(legacy).encode("utf-8"))
    bus = RecordingBus()

    manager = SettingsManager(event_bus=bus, config_file=str(path))

    on_disk = {
        item["key"]: item
        for item in json.loads(path.read_text(encoding="utf-8"))["api_keys_with_status"]
    }
    assert on_disk["UNMAPPABLE_SECRET_KEY"][runtime_field] == value
    assert "status_by_model" not in on_disk["KEY"], "мигрирующий ключ очищается как обычно"
    assert manager.get_key_info("KEY")["status_by_model"]["model"]["requests"] == [10, 10]
    assert "key_runtime_store_failed" not in [event["event"] for event in bus.events]
    assert "UNMAPPABLE_SECRET_KEY" not in json.dumps(bus.events)


def test_corrupt_settings_file_does_not_wipe_runtime_store(tmp_path):
    """Нечитаемый settings.json уводится в карантин с бэкапом, а sidecar — нет.

    Стартовая очистка сирот получала пустой список ключей и вычищала базу
    целиком, безвозвратно унося историю квот вместе с блокировками.
    """
    config = tmp_path / "settings.json"
    manager = SettingsManager(config_file=str(config))
    now = int(time.time())
    _seed_key_runtime(manager, "KEY", {
        "model": {"exhausted_at": now, "exhausted_level": 2, "requests": [now]}
    })
    sidecar = tmp_path / "settings.runtime.sqlite3"
    assert sidecar.exists()

    config.write_text("{ это не json", encoding="utf-8")
    SettingsManager(config_file=str(config))

    state = KeyRuntimeStore(sidecar).load_statuses(["KEY"])["KEY"]["model"]
    assert state.to_dict() == {"exhausted_at": now, "exhausted_level": 2, "requests": [now]}


def test_key_of_unknown_provider_does_not_block_startup(tmp_path):
    """Провайдер могли переименовать или убрать между версиями.

    Миграция бросала ValueError прямо из конструктора SettingsManager, то есть
    старый конфиг делал приложение незапускаемым. Теперь ключ пропускается,
    а его legacy-поля остаются в JSON и ждут возвращения провайдера.
    """
    config = tmp_path / "settings.json"
    config.write_text(json.dumps({
        "api_keys_with_status": [{
            "key": "GHOST",
            "provider": "provider_that_no_longer_exists",
            "requests": [1, 2],
            "exhausted_at": 123,
            "exhausted_level": 1,
        }]
    }), encoding="utf-8")

    SettingsManager(config_file=str(config))

    on_disk = json.loads(config.read_text(encoding="utf-8"))["api_keys_with_status"][0]
    assert on_disk["requests"] == [1, 2]
    assert on_disk["exhausted_at"] == 123
    assert on_disk["exhausted_level"] == 1


def test_unusable_runtime_store_does_not_block_startup(tmp_path):
    """Папка конфига без прав на запись не должна превращаться в кирпич.

    Любая ошибка SQLite на этапе импорта пробрасывалась наружу из конструктора.
    """
    config = tmp_path / "settings.json"
    config.write_text(json.dumps({
        "api_keys_with_status": [{
            "key": "KEY",
            "provider": "gemini",
            "requests": [1],
            "exhausted_at": 5,
            "exhausted_level": 1,
        }]
    }), encoding="utf-8")
    # Каталог на месте файла базы: sqlite не сможет её открыть.
    (tmp_path / "settings.runtime.sqlite3").mkdir()

    SettingsManager(config_file=str(config))

    on_disk = json.loads(config.read_text(encoding="utf-8"))["api_keys_with_status"][0]
    assert on_disk["requests"] == [1]
    assert on_disk["exhausted_at"] == 5


def test_legacy_runtime_survives_until_its_provider_comes_back(tmp_path, monkeypatch):
    """Ключ пропавшего провайдера мигрирует позже, когда провайдер вернётся.

    Маркер однократного импорта к тому моменту уже стоит, поэтому опоздавший
    runtime доливается обычным merge, а не теряется вместе с полями JSON.
    """
    config = tmp_path / "settings.json"
    config.write_text(json.dumps({
        "api_keys_with_status": [
            {"key": "PRESENT", "provider": "gemini", "requests": [7], "exhausted_at": None,
             "exhausted_level": 0},
            {"key": "GHOST", "provider": "temporarily_missing", "requests": [8, 9],
             "exhausted_at": 321, "exhausted_level": 3},
        ]
    }), encoding="utf-8")

    SettingsManager(config_file=str(config))

    after_first = {
        item["key"]: item
        for item in json.loads(config.read_text(encoding="utf-8"))["api_keys_with_status"]
    }
    assert "requests" not in after_first["PRESENT"], "мигрированный ключ должен быть очищен"
    assert after_first["GHOST"]["requests"] == [8, 9], "неперенесённый ключ должен уцелеть"

    # Провайдер вернулся в реестр.
    real_view = api_config.api_providers_view

    def view_with_ghost_provider():
        providers = dict(real_view())
        providers["temporarily_missing"] = {"models": {"m": {"id": "ghost-model"}}}
        return providers

    monkeypatch.setattr(api_config, "api_providers_view", view_with_ghost_provider)

    manager = SettingsManager(config_file=str(config))
    state = manager.get_key_info("GHOST")["status_by_model"]["ghost-model"]
    assert state["requests"] == [8, 9]
    assert state["exhausted_at"] == 321
    assert state["exhausted_level"] == 3


def test_save_key_statuses_does_not_undo_a_block_set_after_the_snapshot(tmp_path):
    """Снимок из интерфейса не должен снимать свежую блокировку.

    Пользователь открыл менеджер ключей, воркер тем временем упёрся в квоту,
    пользователь нажал «Сохранить» — блокировка обязана уцелеть.
    """
    manager = SettingsManager(config_file=str(tmp_path / "settings.json"))
    manager.save_key_statuses([{"key": "KEY", "provider": "gemini"}])
    manager.increment_request_count("KEY", "model")
    snapshot = manager.load_key_statuses()
    assert snapshot[0]["status_by_model"]["model"]["exhausted_at"] is None

    manager.mark_key_as_exhausted("KEY", "model")
    manager.save_key_statuses(snapshot)

    assert manager.get_key_info("KEY")["status_by_model"]["model"]["exhausted_at"] is not None


def test_save_settings_does_not_undo_a_block_set_after_the_snapshot(tmp_path):
    """Тот же снимок приезжает обратно и через load_settings/save_settings."""
    manager = SettingsManager(config_file=str(tmp_path / "settings.json"))
    manager.save_key_statuses([{"key": "KEY", "provider": "gemini"}])
    # Снимок обязан быть непустым: пустой status_by_model до слияния не доходит,
    # и тест разошёлся бы с проверяемым багом.
    manager.increment_request_count("KEY", "model")
    snapshot = manager.load_settings()
    assert snapshot["api_keys_with_status"][0]["status_by_model"]["model"]["exhausted_at"] is None

    manager.mark_key_as_exhausted("KEY", "model")
    snapshot["custom_prompt"] = "правка, сделанная поверх старого снимка"
    manager.save_settings(snapshot)

    assert manager.get_key_info("KEY")["status_by_model"]["model"]["exhausted_at"] is not None
    assert manager.load_settings()["custom_prompt"] == "правка, сделанная поверх старого снимка"


def test_readded_key_does_not_inherit_the_runtime_of_the_removed_one(tmp_path):
    """Удаление ключа через save_key_statuses сразу чистит его runtime.

    Иначе сироты доживали до следующего запуска, и тот же ключ, добавленный
    заново, получал чужой счётчик запросов и чужую 24-часовую блокировку.
    """
    manager = SettingsManager(config_file=str(tmp_path / "settings.json"))
    manager.save_key_statuses([{"key": "KEY", "provider": "gemini"}])
    manager.increment_request_count("KEY", "model")
    manager.mark_key_as_exhausted("KEY", "model")

    manager.save_key_statuses([])
    manager.save_key_statuses([{"key": "KEY", "provider": "gemini"}])

    assert manager.get_key_info("KEY")["status_by_model"] == {}


def test_save_settings_without_key_section_keeps_every_runtime_row(tmp_path):
    """Настройки без раздела ключей ничего не говорят об их составе."""
    manager = SettingsManager(config_file=str(tmp_path / "settings.json"))
    manager.save_key_statuses([{"key": "KEY", "provider": "gemini"}])
    manager.mark_key_as_exhausted("KEY", "model")

    manager.save_settings({"custom_prompt": "без раздела ключей"})

    store = KeyRuntimeStore(tmp_path / "settings.runtime.sqlite3")
    assert store.load_statuses(["KEY"])["KEY"]["model"].exhausted_at is not None
