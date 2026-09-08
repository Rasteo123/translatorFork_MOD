"""SQLite sidecar for mutable API-key runtime state."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime
import hashlib
import os
from pathlib import Path
import sqlite3
import threading
from weakref import WeakValueDictionary


_initialization_registry_lock = threading.Lock()
_initialization_locks: WeakValueDictionary[str, threading.Lock] = WeakValueDictionary()


@contextmanager
def _initialization_lock(path: Path) -> Iterator[None]:
    normalized_path = os.path.normcase(str(path.resolve()))
    with _initialization_registry_lock:
        lock = _initialization_locks.get(normalized_path)
        if lock is None:
            lock = threading.Lock()
            _initialization_locks[normalized_path] = lock
    # Owners and waiters retain a strong reference. The weak registry drops
    # unused entries only after the last user exits, including on exceptions.
    with lock:
        yield


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


def _is_corruption_error(error: sqlite3.DatabaseError) -> bool:
    code = getattr(error, "sqlite_errorcode", None)
    if code is not None:
        return (code & 0xFF) in (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB)
    return str(error) in {"database disk image is malformed", "file is not a database"}


class KeyRuntimeStore:
    """Persist per-key model runtime state without persisting API keys."""

    def __init__(
        self,
        path: Path | str,
        *,
        busy_timeout_ms: int = 5000,
        on_corrupt: Callable[[Path, Path], None] | None = None,
    ) -> None:
        self._path = Path(path)
        self._busy_timeout_ms = busy_timeout_ms
        self._on_corrupt = on_corrupt
        self._ready = False
        self._ready_lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            self.path,
            timeout=self._busy_timeout_ms / 1000,
            isolation_level=None,
        )
        try:
            connection.row_factory = sqlite3.Row
            connection.execute(f"PRAGMA busy_timeout={int(self._busy_timeout_ms)}")
        except BaseException:
            connection.close()
            raise
        return connection

    def ensure_ready(self) -> None:
        if self._ready:
            return

        with self._ready_lock, _initialization_lock(self.path):
            if self._ready:
                return

            backup = None
            try:
                self._initialize()
            except sqlite3.DatabaseError as error:
                if not _is_corruption_error(error):
                    raise
                backup = self._quarantine()
                self._initialize()
            self._ready = True
        if backup is not None and self._on_corrupt is not None:
            self._on_corrupt(self.path, backup)

    def _initialize(self) -> None:
        with closing(self._connect()) as connection:
            checks = connection.execute("PRAGMA quick_check").fetchall()
            if [row[0] for row in checks] != ["ok"]:
                raise sqlite3.DatabaseError("database disk image is malformed")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runtime_meta (
                    schema_version INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS key_model_status (
                    key_hash TEXT NOT NULL,
                    model_name TEXT NOT NULL,
                    exhausted_at REAL,
                    exhausted_level INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (key_hash, model_name)
                );

                CREATE TABLE IF NOT EXISTS key_requests (
                    id INTEGER PRIMARY KEY,
                    key_hash TEXT NOT NULL,
                    model_name TEXT NOT NULL,
                    requested_at REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS key_requests_lookup
                ON key_requests (key_hash, model_name, requested_at, id);
                """
            )
            connection.execute("PRAGMA user_version=1")

    def _quarantine(self) -> Path:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        candidate = self.path.with_name(f"{self.path.name}.corrupt-{stamp}")
        suffix = 0
        while True:
            try:
                # Reserve a unique backup name without overwriting prior evidence.
                with candidate.open("xb"):
                    pass
                break
            except FileExistsError:
                suffix += 1
                candidate = self.path.with_name(f"{self.path.name}.corrupt-{stamp}-{suffix}")
        try:
            self.path.replace(candidate)
        except BaseException:
            candidate.unlink()
            raise
        for extension in ("-wal", "-shm"):
            self.path.with_name(self.path.name + extension).unlink(missing_ok=True)
        return candidate

    def load_statuses(
        self, api_keys: Iterable[str]
    ) -> dict[str, dict[str, ModelRuntimeState]]:
        raw_keys = list(api_keys)
        if not raw_keys:
            return {}

        self.ensure_ready()
        statuses_by_key = {api_key: {} for api_key in raw_keys}
        keys_by_hash: dict[str, list[str]] = defaultdict(list)
        for api_key in statuses_by_key:
            keys_by_hash[key_id(api_key)].append(api_key)

        key_hashes = list(keys_by_hash)
        placeholders = ", ".join("?" for _ in key_hashes)
        requests_by_model: dict[tuple[str, str], list[int]] = defaultdict(list)

        with closing(self._connect()) as connection:
            connection.execute("BEGIN")
            try:
                status_rows = connection.execute(
                    """
                    SELECT key_hash, model_name, exhausted_at, exhausted_level
                    FROM key_model_status
                    WHERE key_hash IN ({placeholders})
                    """.format(placeholders=placeholders),
                    key_hashes,
                ).fetchall()
                request_rows = connection.execute(
                    """
                    SELECT key_hash, model_name, requested_at
                    FROM key_requests
                    WHERE key_hash IN ({placeholders})
                    ORDER BY requested_at, id
                    """.format(placeholders=placeholders),
                    key_hashes,
                ).fetchall()
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            else:
                connection.execute("COMMIT")

        for row in request_rows:
            requests_by_model[(row["key_hash"], row["model_name"])].append(
                row["requested_at"]
            )

        for row in status_rows:
            state = ModelRuntimeState(
                exhausted_at=row["exhausted_at"],
                exhausted_level=row["exhausted_level"],
                requests=tuple(
                    requests_by_model[(row["key_hash"], row["model_name"])]
                ),
            )
            for api_key in keys_by_hash[row["key_hash"]]:
                statuses_by_key[api_key][row["model_name"]] = state

        return statuses_by_key

    def merge_statuses(
        self,
        statuses_by_key: Mapping[str, Mapping[str, Mapping[str, object]]],
    ) -> None:
        if not statuses_by_key:
            return

        self.ensure_ready()
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                for api_key, statuses_by_model in statuses_by_key.items():
                    key_hash = key_id(api_key)
                    for model_name, status in statuses_by_model.items():
                        exhausted_at = status.get("exhausted_at")
                        exhausted_level = status.get("exhausted_level", 0)
                        requests = tuple(status.get("requests", ()))
                        connection.execute(
                            """
                            INSERT INTO key_model_status (
                                key_hash, model_name, exhausted_at, exhausted_level
                            ) VALUES (?, ?, ?, ?)
                            ON CONFLICT(key_hash, model_name) DO UPDATE SET
                                exhausted_at=excluded.exhausted_at,
                                exhausted_level=excluded.exhausted_level
                            """,
                            (key_hash, model_name, exhausted_at, exhausted_level),
                        )

                        existing_requests = Counter(
                            row[0]
                            for row in connection.execute(
                                """
                                SELECT requested_at
                                FROM key_requests
                                WHERE key_hash = ? AND model_name = ?
                                """,
                                (key_hash, model_name),
                            )
                        )
                        incoming_requests = Counter(requests)
                        for requested_at, count in incoming_requests.items():
                            missing = count - existing_requests[requested_at]
                            if missing > 0:
                                connection.executemany(
                                    """
                                    INSERT INTO key_requests (
                                        key_hash, model_name, requested_at
                                    ) VALUES (?, ?, ?)
                                    """,
                                    [(key_hash, model_name, requested_at)] * missing,
                                )
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            else:
                connection.execute("COMMIT")

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        self.ensure_ready()
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.execute("COMMIT")
            except BaseException:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise

    @staticmethod
    def _count_requests(
        connection: sqlite3.Connection, key_hash: str, model_id: str, cutoff: float
    ) -> int:
        return connection.execute(
            """SELECT COUNT(*) FROM key_requests
               WHERE key_hash = ? AND model_name = ? AND requested_at > ?""",
            (key_hash, model_id, cutoff),
        ).fetchone()[0]

    def increment(
        self, api_key: str, model_id: str, requested_at: float, cutoff: float
    ) -> int:
        key_hash = key_id(api_key)
        with self._transaction() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO key_model_status (key_hash, model_name)
                   VALUES (?, ?)""", (key_hash, model_id),
            )
            connection.execute(
                """INSERT INTO key_requests (key_hash, model_name, requested_at)
                   VALUES (?, ?, ?)""", (key_hash, model_id, requested_at),
            )
            connection.execute(
                """DELETE FROM key_requests
                   WHERE key_hash = ? AND model_name = ? AND requested_at <= ?""",
                (key_hash, model_id, cutoff),
            )
            return self._count_requests(connection, key_hash, model_id, cutoff)

    def decrement(
        self, api_key: str, model_id: str, cutoff: float
    ) -> tuple[bool, int]:
        key_hash = key_id(api_key)
        with self._transaction() as connection:
            changed = connection.execute(
                """DELETE FROM key_requests WHERE id = (
                       SELECT id FROM key_requests
                       WHERE key_hash = ? AND model_name = ? AND requested_at > ?
                       ORDER BY requested_at DESC, id DESC LIMIT 1
                   )""", (key_hash, model_id, cutoff),
            ).rowcount > 0
            return changed, self._count_requests(connection, key_hash, model_id, cutoff)

    def set_exhausted(
        self, api_key: str, model_id: str, exhausted_at: float, level: int = 2
    ) -> None:
        with self._transaction() as connection:
            connection.execute(
                """INSERT INTO key_model_status (
                       key_hash, model_name, exhausted_at, exhausted_level
                   ) VALUES (?, ?, ?, ?)
                   ON CONFLICT(key_hash, model_name) DO UPDATE SET
                       exhausted_at=excluded.exhausted_at,
                       exhausted_level=excluded.exhausted_level""",
                (key_id(api_key), model_id, exhausted_at, level),
            )

    def clear_exhaustion(self, api_key: str, model_id: str) -> bool:
        with self._transaction() as connection:
            return connection.execute(
                """UPDATE key_model_status SET exhausted_at = NULL, exhausted_level = 0
                   WHERE key_hash = ? AND model_name = ?
                   AND (exhausted_at IS NOT NULL OR exhausted_level != 0)""",
                (key_id(api_key), model_id),
            ).rowcount > 0

    def maintain_model(
        self, api_key: str, model_id: str, cutoff: float, *,
        clear_exhausted_at: float | None = None,
    ) -> tuple[bool, int, bool]:
        """Prune expired requests and clear only the observed exhaustion value."""
        key_hash = key_id(api_key)
        with self._transaction() as connection:
            changed = connection.execute(
                """DELETE FROM key_requests
                   WHERE key_hash = ? AND model_name = ? AND requested_at <= ?""",
                (key_hash, model_id, cutoff),
            ).rowcount > 0
            cleared = False
            if clear_exhausted_at is not None:
                cleared = connection.execute(
                    """UPDATE key_model_status SET exhausted_at = NULL, exhausted_level = 0
                       WHERE key_hash = ? AND model_name = ? AND exhausted_at = ?""",
                    (key_hash, model_id, clear_exhausted_at),
                ).rowcount > 0
            count = self._count_requests(connection, key_hash, model_id, cutoff)
            return changed, count, cleared

    def prune_requests(
        self, api_key: str, model_id: str, cutoff: float
    ) -> tuple[bool, int]:
        changed, count, _ = self.maintain_model(api_key, model_id, cutoff)
        return changed, count

    def delete_keys(self, api_keys: Iterable[str]) -> None:
        hashes = list({key_id(api_key) for api_key in api_keys})
        if hashes:
            self._delete_hashes(hashes, keep=False)

    def delete_orphans(self, api_keys: Iterable[str]) -> None:
        self._delete_hashes(list({key_id(api_key) for api_key in api_keys}), keep=True)

    def _delete_hashes(self, hashes: list[str], *, keep: bool) -> None:
        placeholders = ", ".join("?" for _ in hashes)
        condition = ""
        if hashes:
            operator = "NOT IN" if keep else "IN"
            condition = f" WHERE key_hash {operator} ({placeholders})"
        with self._transaction() as connection:
            for table in ("key_requests", "key_model_status"):
                connection.execute(f"DELETE FROM {table}{condition}", hashes)
