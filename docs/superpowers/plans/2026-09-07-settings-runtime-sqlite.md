# Settings Runtime SQLite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move mutable API-key request and exhaustion state from `settings.json` into a transactional SQLite sidecar without changing the public `SettingsManager` data shape.

**Architecture:** Add a Qt-independent `KeyRuntimeStore` that stores only SHA-256 key identifiers, per-model exhaustion fields, and individual request rows. Keep key/provider configuration in JSON; make `SettingsManager` assemble and split the legacy-compatible `api_keys_with_status` structure at its boundary.

**Tech Stack:** Python 3.11, standard-library `sqlite3`, PyQt6 signals/timers, pytest, ast-grep MCP; no new runtime dependency.

**Spec:** `docs/superpowers/specs/2026-09-07-settings-runtime-sqlite-design.md`

## Global Constraints

- API keys and providers remain in JSON; only `requests`, `exhausted_at`, and `exhausted_level` move to SQLite.
- The SQLite file is derived from the settings filename: `settings.json` becomes `settings.runtime.sqlite3`.
- Raw API keys must never be persisted in SQLite; rows use a lowercase hexadecimal SHA-256 identifier.
- Existing `SettingsManager` method signatures and returned `api_keys_with_status` shape remain compatible.
- SQLite uses WAL, `busy_timeout=5000`, short-lived connections, and `BEGIN IMMEDIATE` for read-modify-write operations.
- Legacy JSON migration is transactional and idempotent; JSON runtime fields are removed only after the SQLite commit.
- Operational SQLite errors are reported and propagated; they are not silently redirected back into JSON.
- No ORM and no new third-party dependency.
- Production changes follow RED → GREEN; use `/Users/rasreo/dev/translatorFork_MOD/.venv/bin/pytest` from the worktree.
- Use ast-grep MCP for structural consumer discovery and the final no-legacy-write audit.

---

### Task 1: Core `KeyRuntimeStore` schema and lossless reads

**Files:**
- Create: `gemini_translator/utils/key_runtime_store.py`
- Create: `tests/test_key_runtime_store.py`

**Interfaces:**
- Produces: `runtime_store_path(config_file: Path | str) -> Path`
- Produces: `key_id(api_key: str) -> str`
- Produces: immutable `ModelRuntimeState(exhausted_at, exhausted_level, requests)` with `to_dict() -> dict`
- Produces: `KeyRuntimeStore(path, *, busy_timeout_ms=5000, on_corrupt=None)`
- Produces: read-only `KeyRuntimeStore.path: Path`
- Produces: `KeyRuntimeStore.load_statuses(api_keys: Iterable[str]) -> dict[str, dict[str, ModelRuntimeState]]`
- Produces: `KeyRuntimeStore.merge_statuses(statuses_by_key: Mapping[str, Mapping[str, Mapping[str, object]]]) -> None`

- [ ] **Step 1: Write failing path, fingerprint, schema, round-trip, duplicate-timestamp, and secret-absence tests**

Create the test module with these imports, then add tests with literal
expectations:

```python
import hashlib
import sqlite3

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
```

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```bash
/Users/rasreo/dev/translatorFork_MOD/.venv/bin/pytest -q tests/test_key_runtime_store.py
```

Expected: collection fails because `gemini_translator.utils.key_runtime_store` does not exist.

- [ ] **Step 3: Implement the minimal store, schema, and reads**

Use these public definitions:

```python
@dataclass(frozen=True)
class ModelRuntimeState:
    exhausted_at: float | None = None
    exhausted_level: int = 0
    requests: tuple[int, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "exhausted_at": self.exhausted_at,
            "exhausted_level": self.exhausted_level,
            "requests": list(self.requests),
        }


def runtime_store_path(config_file: Path | str) -> Path:
    path = Path(config_file)
    return path.with_name(f"{path.stem}.runtime.sqlite3")


def key_id(api_key: str) -> str:
    return hashlib.sha256(str(api_key).encode("utf-8")).hexdigest()
```

`KeyRuntimeStore._connect()` must create the parent directory, connect with
`timeout=busy_timeout_ms / 1000`, run `PRAGMA busy_timeout=<value>`, and set
`row_factory=sqlite3.Row`. `ensure_ready()` must run schema creation once per
store instance under a `threading.Lock`; applying `PRAGMA journal_mode=WAL`
and `PRAGMA user_version=1` belongs in this initialization.

`merge_statuses()` uses one `BEGIN IMMEDIATE` transaction. Upsert
`key_model_status`; insert only request timestamps not already represented by
the incoming snapshot multiplicity. Use `collections.Counter` over existing
and incoming timestamps so `[90, 90]` stays two rows while repeated saving of
the same snapshot does not add a third row.

`load_statuses()` hashes the requested keys, performs set-based queries for
statuses and requests, orders requests by `requested_at, id`, and maps results
back to the raw keys supplied in memory. It must never write raw keys.
`load_statuses([])` and `merge_statuses({})` return before `ensure_ready()` so
empty public operations do not defeat lazy database creation.

- [ ] **Step 4: Run the store tests and verify GREEN**

Run:

```bash
/Users/rasreo/dev/translatorFork_MOD/.venv/bin/pytest -q tests/test_key_runtime_store.py
```

Expected: all tests pass.

- [ ] **Step 5: Commit the core store**

```bash
git add gemini_translator/utils/key_runtime_store.py tests/test_key_runtime_store.py
git commit -m "feat(settings): add key runtime SQLite store"
```

---

### Task 2: Transactional mutations, concurrency, deletion, and recovery

**Files:**
- Modify: `gemini_translator/utils/key_runtime_store.py`
- Modify: `tests/test_key_runtime_store.py`

**Interfaces:**
- Consumes: `KeyRuntimeStore`, `ModelRuntimeState`, and `key_id` from Task 1
- Produces: `increment(api_key, model_id, requested_at, cutoff) -> int`
- Produces: `decrement(api_key, model_id, cutoff) -> tuple[bool, int]`
- Produces: `set_exhausted(api_key, model_id, exhausted_at, level=2) -> None`
- Produces: `clear_exhaustion(api_key, model_id) -> bool`
- Produces: `prune_requests(api_key, model_id, cutoff) -> tuple[bool, int]`
- Produces: `maintain_model(api_key, model_id, cutoff, *, clear_exhausted_at=None) -> tuple[bool, int, bool]`
- Produces: `delete_keys(api_keys: Iterable[str]) -> None`
- Produces: `delete_orphans(api_keys: Iterable[str]) -> None`
- Produces: corruption callback signature `Callable[[Path, Path], None]`

- [ ] **Step 1: Write failing mutation and two-store concurrency tests**

Add `import threading` and `import pytest` to
`tests/test_key_runtime_store.py`, then append:

```python
def test_increment_from_two_store_instances_loses_no_requests(tmp_path):
    path = tmp_path / "settings.runtime.sqlite3"
    first = KeyRuntimeStore(path)
    second = KeyRuntimeStore(path)
    barrier = threading.Barrier(3)

    def add_many(store, offset):
        barrier.wait()
        for index in range(40):
            store.increment("KEY", "model", 1000 + offset + index, cutoff=0)

    threads = [
        threading.Thread(target=add_many, args=(first, 0)),
        threading.Thread(target=add_many, args=(second, 100)),
    ]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=10)

    assert not any(thread.is_alive() for thread in threads)
    state = first.load_statuses(["KEY"])["KEY"]["model"]
    assert len(state.requests) == 80


def test_decrement_removes_one_latest_request(tmp_path):
    store = KeyRuntimeStore(tmp_path / "runtime.sqlite3")
    store.merge_statuses({"KEY": {"model": {"requests": [10, 20, 20]}}})
    changed, count = store.decrement("KEY", "model", cutoff=0)
    assert (changed, count) == (True, 2)
    assert store.load_statuses(["KEY"])["KEY"]["model"].requests == (10, 20)


def test_exhaustion_and_delete_are_atomic(tmp_path):
    store = KeyRuntimeStore(tmp_path / "runtime.sqlite3")
    store.set_exhausted("KEY", "model", exhausted_at=123.0)
    state = store.load_statuses(["KEY"])["KEY"]["model"]
    assert (state.exhausted_at, state.exhausted_level) == (123.0, 2)
    assert store.clear_exhaustion("KEY", "model") is True
    store.delete_keys(["KEY"])
    assert store.load_statuses(["KEY"]) == {"KEY": {}}


def test_maintenance_is_atomic_and_does_not_clear_a_newer_exhaustion(tmp_path):
    store = KeyRuntimeStore(tmp_path / "runtime.sqlite3")
    store.merge_statuses({
        "KEY": {
            "model": {
                "exhausted_at": 123.0,
                "exhausted_level": 2,
                "requests": [10, 20, 30],
            }
        }
    })

    requests_changed, count, exhaustion_cleared = store.maintain_model(
        "KEY", "model", cutoff=15, clear_exhausted_at=123.0
    )
    assert (requests_changed, count, exhaustion_cleared) == (True, 2, True)

    store.set_exhausted("KEY", "model", exhausted_at=200.0)
    _, _, exhaustion_cleared = store.maintain_model(
        "KEY", "model", cutoff=15, clear_exhausted_at=123.0
    )
    assert exhaustion_cleared is False
    assert store.load_statuses(["KEY"])["KEY"]["model"].exhausted_at == 200.0


def test_delete_orphans_keeps_only_configured_key_ids(tmp_path):
    store = KeyRuntimeStore(tmp_path / "runtime.sqlite3")
    store.merge_statuses({
        "KEEP": {"model": {"requests": [10]}},
        "ORPHAN": {"model": {"requests": [20]}},
    })
    store.delete_orphans(["KEEP"])
    assert store.load_statuses(["KEEP", "ORPHAN"]) == {
        "KEEP": {"model": ModelRuntimeState(requests=(10,))},
        "ORPHAN": {},
    }
```

- [ ] **Step 2: Run mutation tests and verify RED**

Run:

```bash
/Users/rasreo/dev/translatorFork_MOD/.venv/bin/pytest -q tests/test_key_runtime_store.py
```

Expected: failures report missing mutation methods.

- [ ] **Step 3: Implement each mutation as one immediate transaction**

For `increment`, execute delete-before-insert or insert-before-delete within
one `BEGIN IMMEDIATE`, using the exclusive cutoff contract
`requested_at > cutoff`, then return `COUNT(*)`. For `decrement`, delete the
single row selected by:

```sql
SELECT id FROM key_requests
WHERE key_id = ? AND model_id = ? AND requested_at > ?
ORDER BY requested_at DESC, id DESC
LIMIT 1
```

`set_exhausted` upserts the model row. `clear_exhaustion` updates it to
`NULL, 0` and returns whether non-default fields changed. `maintain_model`
deletes old requests and conditionally clears exhaustion in the same
transaction; its `UPDATE` includes the supplied `clear_exhausted_at` in the
`WHERE` clause so a newer concurrent exhaustion cannot be cleared.
`delete_keys` removes explicitly deleted identifiers. `delete_orphans` removes
every identifier absent from the supplied configured-key set; each uses one
transaction and handles an empty iterable without generating invalid SQL.

- [ ] **Step 4: Add corruption classification and quarantine tests**

```python
def test_not_a_database_is_quarantined_and_reported(tmp_path):
    path = tmp_path / "settings.runtime.sqlite3"
    path.write_bytes(b"not sqlite")
    recovered = []
    store = KeyRuntimeStore(path, on_corrupt=lambda source, backup: recovered.append((source, backup)))

    assert store.load_statuses(["KEY"]) == {"KEY": {}}
    assert recovered[0][0] == path
    assert recovered[0][1].read_bytes() == b"not sqlite"


def test_operational_error_is_not_misclassified_as_corruption(tmp_path, monkeypatch):
    store = KeyRuntimeStore(tmp_path / "settings.runtime.sqlite3")
    store.ensure_ready()

    def fail_connect():
        raise sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr(store, "_connect", fail_connect)
    with pytest.raises(sqlite3.OperationalError):
        store.load_statuses(["KEY"])
```

Define `_is_corruption_error()` using SQLite error codes
`SQLITE_CORRUPT`/`SQLITE_NOTADB`, with message fallback only for
`database disk image is malformed` and `file is not a database`. Run
`PRAGMA quick_check` once in `ensure_ready()`. Quarantine the main file with
`.corrupt-YYYYMMDD-HHMMSS`; remove stale `-wal`/`-shm` sidecars only after the
main file has been moved. Permission, full-disk, and busy errors must propagate.

- [ ] **Step 5: Run store tests and verify GREEN**

Run:

```bash
/Users/rasreo/dev/translatorFork_MOD/.venv/bin/pytest -q tests/test_key_runtime_store.py
```

Expected: all core, mutation, concurrency, and recovery tests pass.

- [ ] **Step 6: Commit transactional operations**

```bash
git add gemini_translator/utils/key_runtime_store.py tests/test_key_runtime_store.py
git commit -m "feat(settings): make key runtime updates transactional"
```

---

### Task 3: Compatible read facade in `SettingsManager`

**Files:**
- Modify: `gemini_translator/utils/settings.py:168-630`
- Create: `tests/test_settings_runtime_store.py`
- Modify: `tests/test_settings_live_key_reset.py`

**Interfaces:**
- Consumes: `runtime_store_path`, `KeyRuntimeStore`, and `ModelRuntimeState`
- Produces internally: `_strip_key_runtime_fields(key_info: dict) -> dict`
- Produces internally: `_split_key_statuses(key_statuses: Iterable[dict]) -> tuple[list[dict], dict[str, dict[str, dict]]]`
- Produces internally: `_materialize_key_statuses_unsafe() -> list[dict]`
- Produces internally: `_run_runtime_store_operation(operation: str, callback, *args, **kwargs)`
- Preserves: `load_settings()`, `load_key_statuses()`, `get_key_info()` signatures

- [ ] **Step 1: Write failing compatibility and lazy-creation tests**

Start `tests/test_settings_runtime_store.py` with imports for `json`, `sqlite3`,
`time`, `threading`, `Path`, `pytest`, `QtCore`, `QtWidgets`, `api_config`, and
`SettingsManager`; set `QT_QPA_PLATFORM=offscreen` before importing PyQt, then
add:

```python
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

    expected = {
        "exhausted_at": 10.0,
        "exhausted_level": 2,
        "requests": [1, 2],
    }
    assert manager.load_key_statuses()[0]["status_by_model"]["model"] == expected
    assert manager.get_key_info("KEY")["status_by_model"]["model"] == expected
    assert manager.load_settings()["api_keys_with_status"][0]["status_by_model"]["model"] == expected
    on_disk = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert on_disk["api_keys_with_status"] == [{"key": "KEY", "provider": "gemini"}]


def test_store_operational_error_is_recorded_reported_and_propagated(tmp_path, monkeypatch):
    bus = RecordingBus()
    manager = SettingsManager(event_bus=bus, config_file=str(tmp_path / "settings.json"))
    manager.add_keys_atomically({"KEY"}, "gemini")
    failure = sqlite3.OperationalError("database is locked")

    def fail_load(_keys):
        raise failure

    monkeypatch.setattr(manager._key_runtime_store, "load_statuses", fail_load)
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        manager.load_key_statuses()

    assert manager._last_runtime_store_error is failure
    assert bus.events[-1]["event"] == "key_runtime_store_failed"
    assert bus.events[-1]["data"]["operation"] == "load_key_statuses"


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
```

- [ ] **Step 2: Run the new integration tests and verify RED**

Run:

```bash
/Users/rasreo/dev/translatorFork_MOD/.venv/bin/pytest -q tests/test_settings_runtime_store.py
```

Expected: runtime DB assertions fail because current state remains embedded in JSON.

- [ ] **Step 3: Add the lazy store and materialized read boundary**

Construct `self._key_runtime_store` before initial settings migration, but do
not call `ensure_ready()` in `__init__`. Add:

```python
def _strip_key_runtime_fields(key_info):
    cleaned = deepcopy(key_info)
    for field in ("status_by_model", "requests", "exhausted_at", "exhausted_level"):
        cleaned.pop(field, None)
    return cleaned


def _split_key_statuses(key_statuses):
    configured = []
    runtime = {}
    for item in key_statuses or []:
        copied = deepcopy(item)
        raw_key = copied.get("key")
        model_statuses = copied.get("status_by_model", {})
        if raw_key and isinstance(model_statuses, dict) and model_statuses:
            runtime[raw_key] = deepcopy(model_statuses)
        configured.append(_strip_key_runtime_fields(copied))
    return configured, runtime


def _run_runtime_store_operation(self, operation, callback, *args, **kwargs):
    try:
        result = callback(*args, **kwargs)
    except Exception as error:
        self._last_runtime_store_error = error
        self._post_event("key_runtime_store_failed", {
            "message": str(error),
            "filename": str(self._key_runtime_store.path),
            "operation": operation,
        })
        raise
    self._last_runtime_store_error = None
    return result


def _materialize_key_statuses_unsafe(self):
    configured = [deepcopy(item) for item in self._cache.get("api_keys_with_status", [])]
    raw_keys = [item["key"] for item in configured if item.get("key")]
    if not raw_keys:
        return configured
    runtime = self._run_runtime_store_operation(
        "load_key_statuses",
        self._key_runtime_store.load_statuses,
        raw_keys,
    )
    for item in configured:
        item["status_by_model"] = {
            model_id: state.to_dict()
            for model_id, state in runtime.get(item.get("key"), {}).items()
        }
    return configured
```

`load_settings()` returns a deep copy of `_cache` with the materialized list.
`load_key_statuses()` and `get_key_info()` use the same helper. Do not mutate
`_cache` while assembling output.

Refactor `save_key_statuses()` in the same task so existing UI and reader
callers have a public setup path:

```python
def save_key_statuses(self, key_statuses):
    configured, runtime = _split_key_statuses(key_statuses)
    with self.file_lock:
        self._cache["api_keys_with_status"] = configured
        self._cache.pop("api_keys", None)
        self._save_to_disk_unsafe()
    if runtime:
        self._run_runtime_store_operation(
            "save_key_statuses", self._key_runtime_store.merge_statuses, runtime
        )
    self._post_event("key_statuses_updated")
    return True
```

This persists JSON first, then merges the incoming runtime snapshots, and
publishes `key_statuses_updated` only after both writes succeed.

Catch store operational errors at the `SettingsManager` boundary, assign
`self._last_runtime_store_error`, publish `key_runtime_store_failed` with
`message`, `filename`, and `operation`, then re-raise. The corruption callback
publishes `key_runtime_store_corrupted` with `database_file` and `backup_path`.

- [ ] **Step 4: Replace direct-cache live-reset fixtures with the public save path**

In `tests/test_settings_live_key_reset.py`, replace both assignments to
`manager._cache["api_keys_with_status"]` with
`manager.save_key_statuses(statuses)`. This keeps the tests on the public
contract and ensures the runtime data is routed to SQLite.

- [ ] **Step 5: Run compatible-read and existing key tests**

Run:

```bash
/Users/rasreo/dev/translatorFork_MOD/.venv/bin/pytest -q \
  tests/test_settings_runtime_store.py \
  tests/test_settings_live_key_reset.py \
  tests/test_key_management_widget.py \
  tests/test_reader_key_replacement.py
```

Expected: new read tests pass; any legacy tests that seed `_cache` directly
identify fixtures to migrate to public `save_key_statuses()` in Task 5 rather
than production regressions.

- [ ] **Step 6: Commit the compatible read facade**

```bash
git add gemini_translator/utils/settings.py tests/test_settings_runtime_store.py tests/test_settings_live_key_reset.py
git commit -m "refactor(settings): materialize key runtime state from SQLite"
```

---

### Task 4: Route all runtime mutations through SQLite

**Files:**
- Modify: `gemini_translator/utils/settings.py:445-740,795-811`
- Modify: `tests/test_settings_runtime_store.py`
- Modify: `tests/test_settings_live_key_reset.py`

**Interfaces:**
- Consumes: all transactional methods from Task 2
- Produces internally: `_request_window_cutoff(policy: Mapping, now_ts: int) -> int`
- Preserves: request-count signals and `key_statuses_updated` events after commit

- [ ] **Step 1: Write failing two-manager mutation and event tests**

```python
def test_two_managers_increment_without_lost_updates(tmp_path):
    path = tmp_path / "settings.json"
    first = SettingsManager(config_file=str(path))
    first.add_keys_atomically({"KEY"}, "gemini")
    second = SettingsManager(config_file=str(path))

    barrier = threading.Barrier(3)
    threads = [
        threading.Thread(target=_increment_many, args=(manager, barrier, 30))
        for manager in (first, second)
    ]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=10)

    key_info = first.get_key_info("KEY")
    assert first.get_request_count(key_info, "model") == 60


def test_exhaustion_event_is_emitted_after_store_commit(tmp_path):
    bus = RecordingBus()
    manager = SettingsManager(event_bus=bus, config_file=str(tmp_path / "settings.json"))
    manager.add_keys_atomically({"KEY"}, "gemini")
    assert manager.mark_key_as_exhausted("KEY", "model") is True
    assert manager.get_key_info("KEY")["status_by_model"]["model"]["exhausted_level"] == 2
    assert bus.events[-1]["event"] == "key_statuses_updated"
```

The helper `_increment_many` calls `barrier.wait()` once and then asserts each
`increment_request_count("KEY", "model")` returns `True`.

- [ ] **Step 2: Run mutation tests and verify RED**

Run:

```bash
/Users/rasreo/dev/translatorFork_MOD/.venv/bin/pytest -q \
  tests/test_settings_runtime_store.py -k "increment or exhaustion"
```

Expected: the two-manager count is lower than 60 or runtime state is still written through JSON.

- [ ] **Step 3: Introduce one cutoff calculation used by memory helpers and SQLite**

Extract the daily/rolling/fallback boundary from
`_filter_request_timestamps_in_window()` into:

```python
def _request_window_cutoff(self, policy, now_ts):
    if policy["type"] == "rolling":
        return now_ts - int(policy.get("duration_hours", 24)) * 3600
    if policy["type"] == "daily":
        try:
            tz = ZoneInfo(policy["timezone"])
            now_in_tz = datetime.fromtimestamp(now_ts, tz=timezone.utc).astimezone(tz)
            last_reset = now_in_tz.replace(
                hour=policy.get("reset_hour", 0),
                minute=policy.get("reset_minute", 1),
                second=0,
                microsecond=0,
            )
            if last_reset > now_in_tz:
                last_reset -= timedelta(days=1)
            return int(last_reset.timestamp())
        except ZoneInfoNotFoundError:
            _warn_unknown_timezone_once(
                policy.get("timezone"),
                f"[WARN] Неизвестная таймзона '{policy.get('timezone')}' "
                "в reset_policy, используется деградация до 24ч",
            )
        except Exception:
            pass
    return now_ts - 24 * 3600
```

Make `_filter_request_timestamps_in_window()` filter with the returned cutoff
so the old pure behavior and the new SQL boundary cannot diverge.

- [ ] **Step 4: Replace cache mutations with store transactions**

For each method, briefly hold `file_lock` only to copy the matching key/provider
configuration, then release it before the SQLite call:

- `increment_request_count()` calls `store.increment()` and emits the existing
  `_request_count_changed` signal with its returned count.
- `decrement_request_count()` calls `store.decrement()` and emits only when
  `changed` is true.
- `mark_key_as_exhausted()` calls `store.set_exhausted()` with `time.time()`.
- `clear_key_exhaustion_status()` calls `store.clear_exhaustion()`.
- `_check_and_reset_limits_in_cache()` becomes a runtime maintenance method:
  materialize statuses, calculate each policy cutoff and whether the loaded
  exhaustion is inactive, then call `maintain_model()` once per model. Pass the
  loaded `exhausted_at` only when it should be cleared; the compare-and-clear
  guard preserves a newer exhaustion written by another process.

Do not call `_request_save()` for runtime-only mutations. Preserve current
signals and events after successful SQLite commit.

- [ ] **Step 5: Run the mutation, policy, reader, QA, and UI tests**

Run:

```bash
/Users/rasreo/dev/translatorFork_MOD/.venv/bin/pytest -q \
  tests/test_settings_runtime_store.py \
  tests/test_settings_live_key_reset.py \
  tests/test_key_management_widget.py \
  tests/test_reader_key_replacement.py \
  tests/qa/test_qa_key_rotation.py \
  tests/qa/test_embedding_key_pool.py
```

Expected: all pass and runtime updates no longer activate the JSON save timer.

- [ ] **Step 6: Commit mutation routing**

```bash
git add gemini_translator/utils/settings.py tests/test_settings_runtime_store.py tests/test_settings_live_key_reset.py
git commit -m "refactor(settings): transact key runtime mutations in SQLite"
```

---

### Task 5: Split full saves and migrate legacy JSON exactly once

**Files:**
- Modify: `gemini_translator/utils/key_runtime_store.py`
- Modify: `gemini_translator/utils/settings.py:237-315,387-425,626-705`
- Modify: `tests/test_key_runtime_store.py`
- Modify: `tests/test_settings_runtime_store.py`
- Modify: `tests/test_settings_live_key_reset.py`

**Interfaces:**
- Produces: `KeyRuntimeStore.import_legacy_once(statuses_by_key) -> bool`
- Preserves: `save_settings(settings_dict) -> True`
- Consumes: split `save_key_statuses(key_statuses) -> True` from Task 3

- [ ] **Step 1: Write failing idempotent migration tests**

```python
def test_legacy_json_is_imported_once_and_cleaned(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({
        "api_keys_with_status": [{
            "key": "KEY",
            "provider": "gemini",
            "status_by_model": {
                "model": {"exhausted_at": 50, "exhausted_level": 2, "requests": [10, 10]}
            },
        }]
    }), encoding="utf-8")

    first = SettingsManager(config_file=str(path))
    assert first.get_key_info("KEY")["status_by_model"]["model"]["requests"] == [10, 10]
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["api_keys_with_status"] == [{"key": "KEY", "provider": "gemini"}]

    second = SettingsManager(config_file=str(path))
    assert second.get_key_info("KEY")["status_by_model"]["model"]["requests"] == [10, 10]


def test_committed_import_is_not_repeated_when_json_cleanup_failed(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    legacy = {
        "api_keys_with_status": [{
            "key": "KEY",
            "provider": "gemini",
            "status_by_model": {
                "model": {"exhausted_at": 50, "exhausted_level": 2, "requests": [10, 10]}
            },
        }]
    }
    path.write_text(json.dumps(legacy), encoding="utf-8")

    real_save = SettingsManager._save_unsafe
    calls = 0

    def fail_first_cleanup(self, data):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("disk full")
        return real_save(self, data)

    monkeypatch.setattr(SettingsManager, "_save_unsafe", fail_first_cleanup)
    first = SettingsManager(config_file=str(path))
    assert isinstance(first._last_save_error, OSError)
    assert json.loads(path.read_text(encoding="utf-8")) == legacy

    monkeypatch.setattr(SettingsManager, "_save_unsafe", real_save)
    second = SettingsManager(config_file=str(path))
    assert second.get_key_info("KEY")["status_by_model"]["model"]["requests"] == [10, 10]
    cleaned = json.loads(path.read_text(encoding="utf-8"))
    assert cleaned["api_keys_with_status"] == [{"key": "KEY", "provider": "gemini"}]
```

The first manager must remain usable after the cleanup error: it records and
publishes the failure, while the committed marker prevents the second manager
from importing the two request rows again.

- [ ] **Step 2: Run migration tests and verify RED**

Run:

```bash
/Users/rasreo/dev/translatorFork_MOD/.venv/bin/pytest -q \
  tests/test_settings_runtime_store.py -k "legacy or import"
```

Expected: runtime fields remain in JSON and/or repeated construction duplicates state.

- [ ] **Step 3: Implement `import_legacy_once`**

Use one `BEGIN IMMEDIATE` transaction. Check `runtime_meta` key
`legacy_json_v1_imported`; if present return `False`. Otherwise insert every
legacy status and request exactly as supplied, set the marker, commit, and
return `True`. The marker and rows must roll back together on any exception.

- [ ] **Step 4: Refactor startup migration and JSON stripping**

Split current `_migrate_keys_in_cache()` into:

```python
def _normalize_legacy_key_entries_unsafe(self) -> bool:
    """Normalize top-level requests/exhaustion fields into status_by_model."""

def _extract_runtime_statuses_unsafe(self) -> dict[str, dict[str, dict]]:
    """Return runtime payload keyed by raw key without mutating the cache."""

def _strip_runtime_from_key_cache_unsafe(self) -> None:
    """Replace cached key entries with configuration-only copies."""
```

Startup order is normalize → import transaction → strip cache → atomic JSON
save. If import fails, do not strip or write JSON. If JSON cleanup fails after
commit, record/publish the save error but keep the SQLite migration marker.

- [ ] **Step 5: Split full settings saves and finish key lifecycle handling**

`save_settings()` performs the same split as Task 3's `save_key_statuses()` when
`settings_dict["api_keys_with_status"]` is present. `merge_statuses()` keeps
the maximum multiplicity of each request timestamp already in SQLite or in the
incoming snapshot, preventing stale bulk snapshots from deleting concurrent
increments. Dedicated decrement remains the only supported request removal.

```python
def save_settings(self, settings_dict):
    incoming = deepcopy(settings_dict)
    runtime = None
    if "api_keys_with_status" in incoming:
        configured, runtime = _split_key_statuses(incoming["api_keys_with_status"])
        incoming["api_keys_with_status"] = configured
    with self.file_lock:
        self._cache = incoming
        self._save_to_disk_unsafe()
    if runtime:
        self._run_runtime_store_operation(
            "save_settings", self._key_runtime_store.merge_statuses, runtime
        )
    return True
```

`add_keys_atomically()` stores configuration-only entries. After JSON removal,
`remove_keys_atomically()` calls `delete_keys()`; a failed cleanup may leave an
invisible orphan but must not restore the removed JSON key.

```python
def remove_keys_atomically(self, keys_to_remove):
    with self.file_lock:
        current = self._cache.get("api_keys_with_status", [])
        updated = [item for item in current if item.get("key") not in keys_to_remove]
        removed_count = len(current) - len(updated)
        if removed_count:
            self._cache["api_keys_with_status"] = updated
            self._save_to_disk_unsafe()
    if removed_count:
        self._run_runtime_store_operation(
            "remove_keys", self._key_runtime_store.delete_keys, keys_to_remove
        )
        self._post_event("key_statuses_updated")
    return removed_count
```

At the end of startup, if `self._key_runtime_store.path.exists()` is already
true, call `delete_orphans()` with the configured raw keys. The existence guard
preserves lazy creation, while the cleanup removes leftovers from a previously
failed `delete_keys()` call. Route its operational failures through the same
manager error boundary as other runtime-store operations.

- [ ] **Step 6: Add a public full-save and removal regression test**

Add a test that calls `save_settings()` with one key and runtime state, verifies
the combined shape through `load_settings()`, and verifies that the JSON entry
contains only `key` and `provider`. Then call `remove_keys_atomically({"KEY"})`
and assert both that the public key list is empty and
`manager._key_runtime_store.load_statuses(["KEY"]) == {"KEY": {}}`.

```python
def test_save_settings_splits_runtime_and_removal_deletes_sidecar_state(tmp_path):
    path = tmp_path / "settings.json"
    manager = SettingsManager(config_file=str(path))
    requested_at = int(time.time())
    payload = {
        "custom_prompt": "keep me",
        "api_keys_with_status": [{
            "key": "KEY",
            "provider": "gemini",
            "status_by_model": {
                "model": {
                    "exhausted_at": None,
                    "exhausted_level": 0,
                    "requests": [requested_at],
                }
            },
        }],
    }

    assert manager.save_settings(payload) is True
    assert manager.load_settings()["api_keys_with_status"] == payload["api_keys_with_status"]
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["api_keys_with_status"] == [{"key": "KEY", "provider": "gemini"}]

    assert manager.remove_keys_atomically({"KEY"}) == 1
    assert manager.get_api_keys() == []
    assert manager._key_runtime_store.load_statuses(["KEY"]) == {"KEY": {}}


def test_next_start_removes_orphan_left_by_failed_delete(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    first = SettingsManager(config_file=str(path))
    first.save_key_statuses([{
        "key": "KEY",
        "provider": "gemini",
        "status_by_model": {"model": {"requests": [int(time.time())]}},
    }])

    def fail_delete(_keys):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(first._key_runtime_store, "delete_keys", fail_delete)
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        first.remove_keys_atomically({"KEY"})
    assert first.get_api_keys() == []
    assert first._key_runtime_store.load_statuses(["KEY"])["KEY"]

    second = SettingsManager(config_file=str(path))
    assert second.get_api_keys() == []
    assert second._key_runtime_store.load_statuses(["KEY"]) == {"KEY": {}}
```

- [ ] **Step 7: Run migration, merge, isolation, and corruption suites**

Run:

```bash
/Users/rasreo/dev/translatorFork_MOD/.venv/bin/pytest -q \
  tests/test_key_runtime_store.py \
  tests/test_settings_runtime_store.py \
  tests/test_settings_concurrent_merge.py \
  tests/test_settings_live_key_reset.py \
  tests/test_settings_corrupt_file.py \
  tests/test_settings_isolation.py \
  tests/test_settings_profiles.py \
  tests/test_fix_g00_project_settings_manager_wipes.py
```

Expected: all pass; JSON merge still preserves concurrent key additions and
ordinary settings while runtime state remains in SQLite.

- [ ] **Step 8: Commit migration and split saves**

```bash
git add \
  gemini_translator/utils/key_runtime_store.py \
  gemini_translator/utils/settings.py \
  tests/test_key_runtime_store.py \
  tests/test_settings_runtime_store.py \
  tests/test_settings_live_key_reset.py
git commit -m "refactor(settings): migrate key status out of JSON"
```

---

### Task 6: Remove legacy merge paths and audit every consumer

**Files:**
- Modify: `gemini_translator/utils/settings.py:74-143,237-315`
- Modify only if AST evidence requires it: consumers returned by the searches below
- Modify: tests named by any changed consumer

**Interfaces:**
- Removes: `SettingsManager._merge_disk_timestamps`
- Narrows: `_KEYED_LIST_SETTINGS` continues merging key configuration by `key`
- Preserves: every public key-status method and caller-visible dictionary shape

- [ ] **Step 1: Run AST-grep MCP searches for runtime-field JSON coupling**

Use `mcp__ast_grep__ast_grep_search` with Python patterns:

```text
$OBJ.load_settings()
$OBJ.load_full_session_settings()
$DICT["api_keys_with_status"]
$DICT['api_keys_with_status']
$DICT["status_by_model"]
$DICT['status_by_model']
$DICT.get("status_by_model", $$$ARGS)
$DICT.get('status_by_model', $$$ARGS)
$DICT["requests"] = $VALUE
$DICT['requests'] = $VALUE
$DICT["exhausted_at"] = $VALUE
$DICT['exhausted_at'] = $VALUE
$DICT["exhausted_level"] = $VALUE
$DICT['exhausted_level'] = $VALUE
```

Search production Python files while excluding tests, `.worktrees`, and
`.claude`. For each match that consumes key runtime state, confirm it receives
materialized data from `load_key_statuses()`, `get_key_info()`, or
`load_settings()`. Run both quote variants because ast-grep preserves Python
string-literal spelling for these subscript patterns. Change only confirmed
direct JSON/cache coupling.

The pre-implementation ast-grep 0.45.3 baseline is:

- 8 `load_settings()` calls in QA assembly and four UI modules;
- 12 `load_full_session_settings()` calls in CLI, provider orchestration, and
  three UI modules;
- all 4 direct `api_keys_with_status` subscripts are inside `settings.py`;
- direct `status_by_model` access is limited to `settings.py` and
  `key_management_widget.py`;
- `settings.py` contains 3 single-quoted `requests` assignments, plus 3
  double-quoted and 1 single-quoted assignment for each exhaustion field;
- `key_management_widget.py` contains one returned-copy exhaustion reset which
  is followed by public `save_key_statuses()`; preserve that behavior unless
  its regression test disproves compatibility.

Re-run rather than trusting these counts after Tasks 1-5, because the purpose
of this gate is to audit the actual post-migration tree.

- [ ] **Step 2: Add a regression test for every confirmed coupling before editing it**

Each test must call the real public consumer and assert its observable output;
do not assert source text. Run the individual test and confirm it fails because
the consumer cannot see SQLite-backed state.

- [ ] **Step 3: Remove runtime merge and cache mutation code**

Delete `_merge_disk_timestamps()` and its call in `_save_to_disk_unsafe()`.
Keep `merge_settings_snapshots()` and `_KEYED_LIST_SETTINGS` for concurrent
ordinary settings and key configuration. Remove cache-only request pruning and
runtime-save timer branches made unreachable by Tasks 3-5.

- [ ] **Step 4: Run an AST-grep scan that rejects legacy runtime writes in `settings.py`**

Use `mcp__ast_grep__ast_grep_scan` with inline Python rules covering both
single- and double-quoted variants of assignments to:

```text
$DICT["requests"] = $VALUE
$DICT["exhausted_at"] = $VALUE
$DICT["exhausted_level"] = $VALUE
```

Expected matches: zero in `gemini_translator/utils/settings.py`, except inside
pure returned-copy construction if the implementation uses assignment rather
than a literal. Inspect any remaining match; do not suppress a real cache write.

- [ ] **Step 5: Run the full focused settings/key matrix**

Run:

```bash
/Users/rasreo/dev/translatorFork_MOD/.venv/bin/pytest -q \
  tests/test_key_runtime_store.py \
  tests/test_settings_runtime_store.py \
  tests/test_settings_concurrent_merge.py \
  tests/test_settings_live_key_reset.py \
  tests/test_settings_corrupt_file.py \
  tests/test_settings_isolation.py \
  tests/test_settings_profiles.py \
  tests/test_settings_save_failure.py \
  tests/test_fix_g00_project_settings_manager_wipes.py \
  tests/test_fix_g31_settings_save_unsafe_exception.py \
  tests/test_key_management_widget.py \
  tests/test_reader_key_replacement.py \
  tests/test_reader_voice_sample_key_selection.py \
  tests/qa/test_qa_key_rotation.py \
  tests/qa/test_embedding_key_pool.py
```

Expected: all pass.

- [ ] **Step 6: Run diff hygiene and the complete suite**

Run:

```bash
git diff --check
/Users/rasreo/dev/translatorFork_MOD/.venv/bin/pytest -q
```

The full suite requires permission to open local loopback ports and write the
existing ranobelib test log outside the sandbox. Expected: zero failures; report
the exact pass, skip, warning, and subtest counts from this fresh run.

- [ ] **Step 7: Commit the completed migration**

```bash
git add \
  gemini_translator/utils/settings.py \
  gemini_translator/utils/key_runtime_store.py \
  gemini_translator/ui/widgets/key_management_widget.py \
  tests/test_key_runtime_store.py \
  tests/test_settings_runtime_store.py \
  tests/test_settings_live_key_reset.py \
  tests/test_key_management_widget.py
git commit -m "refactor(settings): retire JSON key runtime state"
```

- [ ] **Step 8: Review commit boundaries and working tree**

Run:

```bash
git status --short
git log --oneline --decorate -8
git diff main...HEAD --stat
```

Expected: clean worktree; separate commits for store, mutations, migration, and
legacy cleanup; no unrelated files from the user's dirty main checkout.
