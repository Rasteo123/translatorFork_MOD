"""SQLite sidecar for mutable API-key runtime state."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import hashlib
from pathlib import Path
import sqlite3
import threading
from typing import Any


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


class KeyRuntimeStore:
    """Persist per-key model runtime state without persisting API keys."""

    def __init__(
        self,
        path: Path | str,
        *,
        busy_timeout_ms: int = 5000,
        on_corrupt: Any = None,
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
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout={int(self._busy_timeout_ms)}")
        return connection

    def ensure_ready(self) -> None:
        if self._ready:
            return

        with self._ready_lock:
            if self._ready:
                return

            with self._connect() as connection:
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

            self._ready = True

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

        with self._connect() as connection:
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
        with self._connect() as connection:
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
