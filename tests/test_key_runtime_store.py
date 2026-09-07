import hashlib
import sqlite3

import pytest

from gemini_translator.utils.key_runtime_store import (
    KeyRuntimeStore,
    ModelRuntimeState,
    key_id,
    runtime_store_path,
)


def test_runtime_store_path_is_adjacent_to_settings(tmp_path):
    settings = tmp_path / "profile.json"
    assert runtime_store_path(settings) == tmp_path / "profile.runtime.sqlite3"


def test_key_id_is_stable_sha256_without_exposing_key():
    assert key_id("SECRET_KEY") == hashlib.sha256(b"SECRET_KEY").hexdigest()


def test_merge_and_load_preserve_duplicate_request_timestamps(tmp_path):
    store = KeyRuntimeStore(tmp_path / "settings.runtime.sqlite3")
    store.merge_statuses({
        "SECRET_KEY": {
            "model-a": {
                "exhausted_at": 100.5,
                "exhausted_level": 2,
                "requests": [90, 90, 100],
            }
        }
    })

    state = store.load_statuses(["SECRET_KEY"])["SECRET_KEY"]["model-a"]
    assert state.to_dict() == {
        "exhausted_at": 100.5,
        "exhausted_level": 2,
        "requests": [90, 90, 100],
    }
    assert b"SECRET_KEY" not in (tmp_path / "settings.runtime.sqlite3").read_bytes()


def test_load_keeps_models_and_keys_independent(tmp_path):
    store = KeyRuntimeStore(tmp_path / "settings.runtime.sqlite3")
    store.merge_statuses({
        "KEY_A": {"model-a": {"requests": [10]}},
        "KEY_B": {"model-b": {"requests": [20]}},
    })
    loaded = store.load_statuses(["KEY_A", "KEY_B"])
    assert loaded["KEY_A"]["model-a"].requests == (10,)
    assert "model-b" not in loaded["KEY_A"]
    assert loaded["KEY_B"]["model-b"].requests == (20,)


def test_schema_version_wal_and_busy_timeout_are_configured(tmp_path):
    path = tmp_path / "settings.runtime.sqlite3"
    store = KeyRuntimeStore(path)
    store.ensure_ready()

    with sqlite3.connect(path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {"runtime_meta", "key_model_status", "key_requests"} <= tables
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"

    with store._connect() as connection:
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


def test_repeated_merge_does_not_add_duplicate_snapshot_requests(tmp_path):
    store = KeyRuntimeStore(tmp_path / "settings.runtime.sqlite3")
    statuses = {"KEY": {"model": {"requests": [90, 90]}}}

    store.merge_statuses(statuses)
    store.merge_statuses(statuses)

    assert store.load_statuses(["KEY"])["KEY"]["model"].requests == (90, 90)


def test_empty_merge_does_not_create_database(tmp_path):
    path = tmp_path / "settings.runtime.sqlite3"

    KeyRuntimeStore(path).merge_statuses({})

    assert not path.exists()


def test_empty_load_does_not_create_database(tmp_path):
    path = tmp_path / "settings.runtime.sqlite3"

    assert KeyRuntimeStore(path).load_statuses([]) == {}
    assert not path.exists()


def test_path_is_read_only(tmp_path):
    store = KeyRuntimeStore(tmp_path / "settings.runtime.sqlite3")

    with pytest.raises(AttributeError):
        store.path = tmp_path / "other.runtime.sqlite3"
