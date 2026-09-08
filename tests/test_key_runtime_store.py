import gc
import hashlib
import os
import sqlite3
import threading
import weakref

import pytest

import gemini_translator.utils.key_runtime_store as key_runtime_store_module
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


def test_public_operations_explicitly_close_every_sqlite_connection(
    tmp_path, monkeypatch
):
    real_connect = sqlite3.connect
    closed_connections = []

    class TrackingConnection(sqlite3.Connection):
        def close(self):
            closed_connections.append(self)
            super().close()

    def tracking_connect(*args, **kwargs):
        kwargs["factory"] = TrackingConnection
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(key_runtime_store_module.sqlite3, "connect", tracking_connect)
    store = KeyRuntimeStore(tmp_path / "settings.runtime.sqlite3")

    store.merge_statuses({"KEY": {"model": {"requests": [10]}}})
    store.load_statuses(["KEY"])

    assert len(closed_connections) == 3


def test_load_statuses_returns_one_snapshot_when_merge_happens_between_selects(
    tmp_path, monkeypatch
):
    path = tmp_path / "settings.runtime.sqlite3"
    writer = KeyRuntimeStore(path)
    writer.merge_statuses({
        "KEY": {
            "model": {
                "exhausted_level": 1,
                "requests": [10],
            }
        }
    })
    reader = KeyRuntimeStore(path)
    reader.ensure_ready()

    real_connect = sqlite3.connect
    triggered = False

    class InterleavingConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            nonlocal triggered
            cursor = super().execute(sql, parameters)
            if "FROM key_model_status" in sql and not triggered:
                triggered = True
                writer.merge_statuses({
                    "KEY": {
                        "model": {
                            "exhausted_level": 2,
                            "requests": [10, 20],
                        }
                    }
                })
            return cursor

    def interleaving_connect(*args, **kwargs):
        kwargs["factory"] = InterleavingConnection
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(
        key_runtime_store_module.sqlite3, "connect", interleaving_connect
    )

    state = reader.load_statuses(["KEY"])["KEY"]["model"]

    assert triggered
    assert state.to_dict() == {
        "exhausted_at": None,
        "exhausted_level": 1,
        "requests": [10],
    }


def test_increment_from_two_store_instances_loses_no_requests(tmp_path):
    path = tmp_path / "settings.runtime.sqlite3"
    first, second = KeyRuntimeStore(path), KeyRuntimeStore(path)
    barrier = threading.Barrier(3)
    errors = []

    def add_many(store, offset):
        try:
            barrier.wait()
            for index in range(40):
                store.increment("KEY", "model", 1000 + offset + index, cutoff=0)
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=add_many, args=(first, 0)),
               threading.Thread(target=add_many, args=(second, 100))]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=10)
    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    assert len(first.load_statuses(["KEY"])["KEY"]["model"].requests) == 80


def test_increment_prunes_exclusive_cutoff_and_preserves_other_models(tmp_path):
    store = KeyRuntimeStore(tmp_path / "runtime.sqlite3")
    store.merge_statuses({"KEY": {"model": {"requests": [10, 20, 30]},
                                 "other": {"requests": [10]}}})
    assert store.increment("KEY", "model", 40, cutoff=20) == 2
    assert store.increment("KEY", "model", 20, cutoff=20) == 2
    loaded = store.load_statuses(["KEY"])["KEY"]
    assert loaded["model"].requests == (30, 40)
    assert loaded["other"].requests == (10,)


def test_decrement_removes_one_latest_request(tmp_path):
    store = KeyRuntimeStore(tmp_path / "runtime.sqlite3")
    store.merge_statuses({"KEY": {"model": {"requests": [10, 20, 20]}}})
    assert store.decrement("KEY", "model", cutoff=0) == (True, 2)
    assert store.load_statuses(["KEY"])["KEY"]["model"].requests == (10, 20)
    assert store.decrement("KEY", "model", cutoff=20) == (False, 0)
    assert store.decrement("MISSING", "model", cutoff=0) == (False, 0)


def test_exhaustion_and_delete_are_atomic(tmp_path):
    store = KeyRuntimeStore(tmp_path / "runtime.sqlite3")
    assert store.clear_exhaustion("KEY", "model") is False
    store.set_exhausted("KEY", "model", exhausted_at=123.0)
    state = store.load_statuses(["KEY"])["KEY"]["model"]
    assert (state.exhausted_at, state.exhausted_level) == (123.0, 2)
    assert store.clear_exhaustion("KEY", "model") is True
    assert store.clear_exhaustion("KEY", "model") is False
    store.increment("KEY", "model", 10, cutoff=0)
    store.delete_keys(iter(["KEY"]))
    assert store.load_statuses(["KEY"]) == {"KEY": {}}
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM key_requests").fetchone()[0] == 0


def test_maintenance_is_atomic_and_does_not_clear_a_newer_exhaustion(tmp_path):
    store = KeyRuntimeStore(tmp_path / "runtime.sqlite3")
    store.merge_statuses({"KEY": {"model": {
        "exhausted_at": 123.0, "exhausted_level": 2, "requests": [10, 20, 30],
    }}})
    assert store.maintain_model("KEY", "model", cutoff=15,
                                clear_exhausted_at=123.0) == (True, 2, True)
    store.set_exhausted("KEY", "model", exhausted_at=200.0)
    assert store.maintain_model("KEY", "model", cutoff=15,
                                clear_exhausted_at=123.0) == (False, 2, False)
    assert store.load_statuses(["KEY"])["KEY"]["model"].exhausted_at == 200.0
    assert store.prune_requests("KEY", "model", cutoff=20) == (True, 1)
    assert store.maintain_model("MISSING", "model", cutoff=0) == (False, 0, False)


def test_delete_orphans_keeps_only_configured_key_ids(tmp_path):
    store = KeyRuntimeStore(tmp_path / "runtime.sqlite3")
    store.merge_statuses({"KEEP": {"model": {"requests": [10]}},
                          "ORPHAN": {"model": {"requests": [20]}}})
    store.delete_keys([])
    store.delete_orphans(iter(["KEEP"]))
    assert store.load_statuses(["KEEP", "ORPHAN"]) == {
        "KEEP": {"model": ModelRuntimeState(requests=(10,))}, "ORPHAN": {},
    }
    store.delete_orphans([])
    assert store.load_statuses(["KEEP"]) == {"KEEP": {}}
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM key_requests").fetchone()[0] == 0


def test_not_a_database_is_quarantined_and_reported(tmp_path):
    path = tmp_path / "settings.runtime.sqlite3"
    path.write_bytes(b"not sqlite")
    recovered = []
    store = KeyRuntimeStore(path, on_corrupt=lambda source, backup: recovered.append((source, backup)))
    assert store.load_statuses(["KEY"]) == {"KEY": {}}
    assert len(recovered) == 1
    assert recovered[0][0] == path
    assert recovered[0][1].read_bytes() == b"not sqlite"
    assert recovered[0][1].name.startswith("settings.runtime.sqlite3.corrupt-")
    store.increment("KEY", "model", 10, cutoff=0)
    assert store.load_statuses(["KEY"])["KEY"]["model"].requests == (10,)


@pytest.mark.parametrize("message", ["unable to open database file", "database is locked",
                                      "database or disk is full", "attempt to write a readonly database"])
def test_operational_error_is_not_misclassified_as_corruption(tmp_path, monkeypatch, message):
    store = KeyRuntimeStore(tmp_path / "settings.runtime.sqlite3")
    store.ensure_ready()

    def fail_connect():
        raise sqlite3.OperationalError(message)

    monkeypatch.setattr(store, "_connect", fail_connect)
    with pytest.raises(sqlite3.OperationalError, match=message):
        store.load_statuses(["KEY"])
    store._ready = False
    with pytest.raises(sqlite3.OperationalError, match=message):
        store.ensure_ready()
    assert list(tmp_path.glob("*.corrupt-*")) == []


@pytest.mark.parametrize("code,message,expected", [
    (sqlite3.SQLITE_CORRUPT, "unrelated text", True),
    (sqlite3.SQLITE_NOTADB, "unrelated text", True),
    (sqlite3.SQLITE_CORRUPT | (1 << 8), "extended error", True),
    (sqlite3.SQLITE_BUSY, "file is not a database", False),
    (None, "database disk image is malformed", True),
    (None, "file is not a database", True),
    (None, "possibly file is not a database", False),
])
def test_corruption_classification_is_narrow(code, message, expected):
    error = sqlite3.DatabaseError(message)
    if code is not None:
        error.sqlite_errorcode = code
    assert key_runtime_store_module._is_corruption_error(error) is expected


def test_quick_check_runs_once_and_detects_reported_corruption(tmp_path, monkeypatch):
    path = tmp_path / "runtime.sqlite3"
    KeyRuntimeStore(path).merge_statuses({"KEY": {"model": {"requests": [10]}}})
    real_connect = sqlite3.connect
    checks = []
    recovered = []

    class CheckConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if sql == "PRAGMA quick_check":
                checks.append(sql)
                if len(checks) == 1:
                    return super().execute("SELECT 'bad page'")
            return super().execute(sql, parameters)

    def checked_connect(*args, **kwargs):
        return real_connect(*args, factory=CheckConnection, **kwargs)

    monkeypatch.setattr(key_runtime_store_module.sqlite3, "connect", checked_connect)
    store = KeyRuntimeStore(path, on_corrupt=lambda *paths: recovered.append(paths))
    assert store.load_statuses(["KEY"]) == {"KEY": {}}
    store.load_statuses(["KEY"])
    assert len(checks) == 2  # Original file and recreated database, never per operation.
    assert len(recovered) == 1


def test_maintenance_rolls_back_pruning_when_clear_fails(tmp_path):
    store = KeyRuntimeStore(tmp_path / "runtime.sqlite3")
    store.merge_statuses({"KEY": {"model": {
        "requests": [10, 20], "exhausted_at": 123.0, "exhausted_level": 2,
    }}})
    with sqlite3.connect(store.path) as connection:
        connection.execute("""CREATE TRIGGER reject_clear BEFORE UPDATE ON key_model_status
                            BEGIN SELECT RAISE(ABORT, 'clear failed'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="clear failed"):
        store.maintain_model("KEY", "model", cutoff=15, clear_exhausted_at=123.0)
    assert store.load_statuses(["KEY"])["KEY"]["model"] == ModelRuntimeState(
        requests=(10, 20), exhausted_at=123.0, exhausted_level=2,
    )


def test_mutations_close_connections_after_success_and_rollback(tmp_path, monkeypatch):
    real_connect = sqlite3.connect
    connections = []

    def tracking_connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr(key_runtime_store_module.sqlite3, "connect", tracking_connect)
    store = KeyRuntimeStore(tmp_path / "runtime.sqlite3")
    store.increment("KEY", "model", 10, 0)
    store.decrement("KEY", "model", 0)
    store.set_exhausted("KEY", "model", 1)
    store.clear_exhaustion("KEY", "model")
    store.maintain_model("KEY", "model", 0)
    store.delete_keys(["KEY"])
    store.delete_orphans([])
    with pytest.raises(sqlite3.IntegrityError):
        store.increment("KEY", "model", None, 0)
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")


def test_connection_configuration_failure_closes_connection(tmp_path, monkeypatch):
    real_connect = sqlite3.connect
    connections = []

    class FailingConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            raise sqlite3.OperationalError("configuration failed")

    def failing_connect(*args, **kwargs):
        connection = real_connect(*args, factory=FailingConnection, **kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr(key_runtime_store_module.sqlite3, "connect", failing_connect)
    with pytest.raises(sqlite3.OperationalError, match="configuration failed"):
        KeyRuntimeStore(tmp_path / "runtime.sqlite3").ensure_ready()
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connections[0].cursor()


def test_quarantine_moves_main_before_removing_sidecars_and_keeps_backups(tmp_path, monkeypatch):
    path = tmp_path / "runtime.sqlite3"
    wal, shm = tmp_path / "runtime.sqlite3-wal", tmp_path / "runtime.sqlite3-shm"
    real_unlink = type(path).unlink

    def checked_unlink(target, *args, **kwargs):
        if target in (wal, shm):
            assert not path.exists()
            assert any(backup.read_bytes() == b"broken" for backup in tmp_path.glob("*.corrupt-*"))
        return real_unlink(target, *args, **kwargs)

    monkeypatch.setattr(type(path), "unlink", checked_unlink)
    store = KeyRuntimeStore(path)
    backups = []
    for _ in range(2):
        path.write_bytes(b"broken")
        wal.write_bytes(b"stale wal")
        shm.write_bytes(b"stale shm")
        backups.append(store._quarantine())
        assert not wal.exists() and not shm.exists()
    assert backups[0] != backups[1]
    assert [backup.read_bytes() for backup in backups] == [b"broken", b"broken"]


def test_quarantine_preserves_main_and_sidecars_if_move_fails(tmp_path, monkeypatch):
    path = tmp_path / "runtime.sqlite3"
    wal = tmp_path / "runtime.sqlite3-wal"
    path.write_bytes(b"broken")
    wal.write_bytes(b"preserve wal")

    def fail_move(*args, **kwargs):
        raise PermissionError("move denied")

    monkeypatch.setattr(type(path), "replace", fail_move)
    with pytest.raises(PermissionError, match="move denied"):
        KeyRuntimeStore(path)._quarantine()
    assert path.read_bytes() == b"broken"
    assert wal.read_bytes() == b"preserve wal"
    assert list(tmp_path.glob("*.corrupt-*")) == []


def test_delete_keys_rolls_back_request_removal_when_status_delete_fails(tmp_path):
    store = KeyRuntimeStore(tmp_path / "runtime.sqlite3")
    store.merge_statuses({"KEY": {"model": {"requests": [10]}}})
    with sqlite3.connect(store.path) as connection:
        connection.execute("""CREATE TRIGGER reject_delete BEFORE DELETE ON key_model_status
                            BEGIN SELECT RAISE(ABORT, 'delete failed'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="delete failed"):
        store.delete_keys(["KEY"])
    assert store.load_statuses(["KEY"])["KEY"]["model"].requests == (10,)


@pytest.mark.parametrize("same_path", [False, True])
def test_paused_initialization_only_blocks_the_same_normalized_path(
    tmp_path, monkeypatch, same_path
):
    first = KeyRuntimeStore(tmp_path / "first.sqlite3")
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path, target_is_directory=True)
    second = KeyRuntimeStore(
        alias / "first.sqlite3" if same_path else tmp_path / "second.sqlite3"
    )
    first_started = threading.Event()
    release_first = threading.Event()
    second_started = threading.Event()
    second_finished = threading.Event()
    errors = []
    initialize_first = first._initialize

    def pause_first():
        first_started.set()
        if not release_first.wait(timeout=5):
            raise TimeoutError("first initialization was not released")
        initialize_first()

    def run_first():
        try:
            first.ensure_ready()
        except BaseException as error:
            errors.append(error)

    def run_second():
        second_started.set()
        try:
            second.ensure_ready()
        except BaseException as error:
            errors.append(error)
        finally:
            second_finished.set()

    monkeypatch.setattr(first, "_initialize", pause_first)
    threads = [threading.Thread(target=run_first), threading.Thread(target=run_second)]
    threads[0].start()
    try:
        assert first_started.wait(timeout=2)
        threads[1].start()
        assert second_started.wait(timeout=2)
        assert second_finished.wait(timeout=0.2 if same_path else 2) is not same_path
    finally:
        release_first.set()
        for thread in threads:
            if thread.ident is not None:
                thread.join(timeout=5)
    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    first.increment("KEY", "model", 10, 0)
    second.increment("KEY", "model", 20, 0)
    expected = (10, 20) if same_path else (10,)
    assert first.load_statuses(["KEY"])["KEY"]["model"].requests == expected


@pytest.mark.parametrize("fail", [False, True])
def test_initialization_registry_releases_unused_locks(tmp_path, fail):
    path = tmp_path / "runtime.sqlite3"
    normalized_path = os.path.normcase(str(path.resolve()))
    lock_reference = None
    try:
        with key_runtime_store_module._initialization_lock(path):
            lock_reference = weakref.ref(
                key_runtime_store_module._initialization_locks[normalized_path]
            )
            assert lock_reference() is not None
            if fail:
                raise RuntimeError("initialization failed")
    except RuntimeError:
        pass
    gc.collect()
    assert lock_reference is not None and lock_reference() is None
    assert normalized_path not in key_runtime_store_module._initialization_locks
