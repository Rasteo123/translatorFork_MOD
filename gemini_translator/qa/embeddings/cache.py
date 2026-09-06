"""Qt-free, disposable content-addressed storage for semantic embeddings."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import unicodedata
import zipfile

import numpy as np

from .base import (
    EmbeddingBatch,
    EmbeddingContractError,
    EmbeddingProvider,
    EmbeddingRequest,
    validate_and_normalize_batch,
)


_INDEX_SCHEMA_VERSION = 2
_SAFE_PROVIDER_ID = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_SHA256_HEX = re.compile(r"[0-9a-f]{64}\Z")
# Имя шарда: либо старый двухсимвольный префикс digest, либо шард одной пачки
# put_many («b» + хэш набора digest'ов). Пачечные шарды пишутся один раз и никогда
# не перечитываются-переписываются при добавлении: стоимость put_many не растёт с
# размером кэша.
_SHARD_NAME = re.compile(r"(?:[0-9a-f]{2}|b[0-9a-f]{8,32})\Z")
# Изменения индекса не переписывают index.json целиком: каждая пачка put_many и
# каждое обновление last_access дописываются одной строкой в журнал index.log
# (O(пачки) и один fsync). Журнал проигрывается поверх index.json при чтении и
# сворачивается в новый index.json (компакция), когда вырастает за порог, при
# flush() или если базовый файл оказался повреждён. Диск остаётся
# источником истины: запись видна другим экземплярам и процессам сразу.
_JOURNAL_COMPACT_LINES = 256
_JOURNAL_COMPACT_BYTES = 8 * 1024 * 1024


class _RootState:
    """Разделяемое между экземплярами состояние одного корня кэша."""

    __slots__ = ("lock", "index", "fingerprint", "journal_lines", "needs_compaction")

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.index: dict[str, object] | None = None
        self.fingerprint: tuple[object, object] | None = None
        self.journal_lines = 0
        self.needs_compaction = False
_ENTRY_FIELDS = frozenset(
    {
        "normalized_text",
        "language",
        "provider",
        "model",
        "dimensions",
        "task_type",
        "preprocessing_identity",
        "shard",
        "last_access",
    }
)
_DIMENSIONLESS_FIELDS = frozenset(
    {
        "normalized_text",
        "language",
        "provider",
        "model",
        "dimensions",
        "task_type",
        "preprocessing_identity",
    }
)


def _nonempty(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EmbeddingContractError(f"{field_name} must be a nonempty string")
    return value.strip()


def _positive_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise EmbeddingContractError(f"{field_name} must be a positive integer")
    return value


def _normalized_text(value: object) -> str:
    text = _nonempty(value, "text")
    normalized = " ".join(unicodedata.normalize("NFKC", text).split()).casefold()
    if not normalized:
        raise EmbeddingContractError("normalized text must be nonempty")
    return normalized


def _normalized_language(value: object) -> str:
    language = _nonempty(value, "language").replace("_", "-")
    base = language.split("-", 1)[0].casefold()
    if not base:
        raise EmbeddingContractError("language must have a nonempty base language")
    return base


def _canonical_provider_id(value: object) -> str:
    provider = _nonempty(value, "provider").casefold()
    if not _SAFE_PROVIDER_ID.fullmatch(provider):
        raise EmbeddingContractError("provider name must be a safe canonical identifier")
    return provider


def _identity_digest(parts: Sequence[object]) -> str:
    payload = json.dumps(tuple(parts), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class EmbeddingCacheKey:
    """Immutable identity for one vector in one exact embedding space."""

    normalized_text: str
    language: str
    provider: str
    model: str
    dimensions: int
    task_type: str
    preprocessing_identity: str
    digest: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "normalized_text", _normalized_text(self.normalized_text))
        object.__setattr__(self, "language", _normalized_language(self.language))
        object.__setattr__(self, "provider", _canonical_provider_id(self.provider))
        object.__setattr__(self, "model", _nonempty(self.model, "model"))
        object.__setattr__(self, "dimensions", _positive_int(self.dimensions, "dimensions"))
        object.__setattr__(self, "task_type", _nonempty(self.task_type, "task_type"))
        object.__setattr__(
            self,
            "preprocessing_identity",
            _nonempty(self.preprocessing_identity, "preprocessing_identity"),
        )
        object.__setattr__(
            self,
            "digest",
            _identity_digest(
                (
                    self.normalized_text,
                    self.language,
                    self.provider,
                    self.model,
                    self.dimensions,
                    self.task_type,
                    self.preprocessing_identity,
                )
            ),
        )

    @classmethod
    def from_text(
        cls,
        text: str,
        *,
        language: str,
        provider: str,
        model: str,
        dimensions: int,
        task_type: str,
        preprocessing_identity: str,
    ) -> "EmbeddingCacheKey":
        return cls(
            normalized_text=text,
            language=language,
            provider=provider,
            model=model,
            dimensions=dimensions,
            task_type=task_type,
            preprocessing_identity=preprocessing_identity,
        )


def _dimensionless_digest(
    normalized_text: str,
    language: str,
    provider: str,
    model: str,
    task_type: str,
    preprocessing_identity: str,
) -> str:
    return _identity_digest(
        (
            normalized_text,
            language,
            provider,
            model,
            task_type,
            preprocessing_identity,
        )
    )


def _empty_index() -> dict[str, object]:
    return {
        "schema_version": _INDEX_SCHEMA_VERSION,
        "access_counter": 0,
        "entries": {},
        "dimensionless": {},
    }


def _entry_for(
    key: EmbeddingCacheKey, last_access: int, shard: str | None = None
) -> dict[str, object]:
    return {
        "normalized_text": key.normalized_text,
        "language": key.language,
        "provider": key.provider,
        "model": key.model,
        "dimensions": key.dimensions,
        "task_type": key.task_type,
        "preprocessing_identity": key.preprocessing_identity,
        "shard": key.digest[:2] if shard is None else shard,
        "last_access": last_access,
    }


def _batch_shard_name(digests: Sequence[str]) -> str:
    joined = "|".join(sorted(digests)).encode("ascii")
    return "b" + hashlib.sha256(joined).hexdigest()[:16]


def _dimensionless_entry(key: EmbeddingCacheKey) -> dict[str, object]:
    return {
        "normalized_text": key.normalized_text,
        "language": key.language,
        "provider": key.provider,
        "model": key.model,
        "dimensions": key.dimensions,
        "task_type": key.task_type,
        "preprocessing_identity": key.preprocessing_identity,
    }


class EmbeddingCache:
    """Process-safe local cache whose entire root is safe to discard."""

    _states_guard = threading.Lock()
    _states: dict[str, _RootState] = {}

    def __init__(self, root: str | os.PathLike[str]) -> None:
        if not isinstance(root, (str, os.PathLike)):
            raise TypeError("root must be a filesystem path")
        self.root = Path(root).expanduser().resolve()
        self.index_path = self.root / "index.json"
        self.journal_path = self.root / "index.log"
        state_key = os.fspath(self.root)
        with self._states_guard:
            self._state = self._states.setdefault(state_key, _RootState())
        self._lock = self._state.lock

    def get_many(
        self, keys: Sequence[EmbeddingCacheKey]
    ) -> dict[EmbeddingCacheKey, np.ndarray]:
        unique = self._unique_keys(keys)
        if not unique:
            return {}
        with self._lock:
            index = self._index_unlocked()
            return self._get_many_unlocked(unique, index)

    def put_many(self, values: Mapping[EmbeddingCacheKey, np.ndarray]) -> None:
        self._put_many(values, dimensionless=False)

    def flush(self) -> bool:
        """Сворачивает журнал в index.json (компакция)."""
        with self._lock:
            index = self._index_unlocked()
            if self._state.journal_lines == 0 and not self._state.needs_compaction:
                return True
            return self._compact_unlocked(index)

    def close(self) -> None:
        self.flush()

    # ---- индекс в памяти, синхронизированный с диском ----

    def _fingerprint_unlocked(self) -> tuple[object, object]:
        def stamp(path: Path) -> object:
            try:
                stat = path.stat()
            except OSError:
                return None
            return (stat.st_size, stat.st_mtime_ns, stat.st_ino)

        return (stamp(self.index_path), stamp(self.journal_path))

    def _index_unlocked(self) -> dict[str, object]:
        """Индекс = index.json + журнал; перечитывается, только если файлы менялись извне."""
        state = self._state
        fingerprint = self._fingerprint_unlocked()
        if state.index is None or state.fingerprint != fingerprint:
            state.index = self._load_index_unlocked()
            state.fingerprint = fingerprint
        return state.index

    def _refresh_fingerprint_unlocked(self) -> None:
        self._state.fingerprint = self._fingerprint_unlocked()

    def _journal_bytes_unlocked(self) -> int:
        try:
            return self.journal_path.stat().st_size
        except OSError:
            return 0

    def _record_change_unlocked(self, index: dict[str, object], record: dict[str, object]) -> None:
        """Фиксирует изменение на диске: строкой журнала или, если пора, компакцией."""
        state = self._state
        if (
            state.needs_compaction
            or state.journal_lines >= _JOURNAL_COMPACT_LINES
            or self._journal_bytes_unlocked() >= _JOURNAL_COMPACT_BYTES
        ):
            if self._compact_unlocked(index):
                return
        if not self._append_journal_unlocked(record):
            self._compact_unlocked(index)

    def _append_journal_unlocked(self, record: Mapping[str, object]) -> bool:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            line = json.dumps(
                record,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            with self.journal_path.open("a", encoding="utf-8") as stream:
                stream.write(line + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        except (OSError, TypeError, ValueError):
            return False
        self._state.journal_lines += 1
        self._refresh_fingerprint_unlocked()
        return True

    def _compact_unlocked(self, index: dict[str, object]) -> bool:
        if not self._atomic_write_json_unlocked(index):
            return False
        try:
            self.journal_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            return False
        self._state.journal_lines = 0
        self._state.needs_compaction = False
        self._refresh_fingerprint_unlocked()
        return True

    @staticmethod
    def _unique_keys(keys: Sequence[EmbeddingCacheKey]) -> tuple[EmbeddingCacheKey, ...]:
        if not isinstance(keys, Sequence) or isinstance(keys, (str, bytes, bytearray)):
            raise TypeError("keys must be a sequence of EmbeddingCacheKey values")
        unique: list[EmbeddingCacheKey] = []
        seen: set[EmbeddingCacheKey] = set()
        for key in keys:
            if not isinstance(key, EmbeddingCacheKey):
                raise TypeError("keys must contain EmbeddingCacheKey values")
            if key not in seen:
                seen.add(key)
                unique.append(key)
        return tuple(unique)

    def _put_many(
        self,
        values: Mapping[EmbeddingCacheKey, np.ndarray],
        *,
        dimensionless: bool,
    ) -> None:
        if not isinstance(values, Mapping):
            raise TypeError("values must be a mapping")
        prepared: dict[EmbeddingCacheKey, np.ndarray] = {}
        for key, vector in values.items():
            if not isinstance(key, EmbeddingCacheKey):
                raise TypeError("cache values must use EmbeddingCacheKey keys")
            prepared[key] = self._prepare_vector(vector, key.dimensions)
        if not prepared:
            return

        with self._lock:
            index = self._index_unlocked()
            # Вся пачка уходит в один новый шард: одна запись и один fsync на put_many,
            # без чтения и перезаписи уже накопленных шардов.
            vectors = {key.digest: vector for key, vector in prepared.items()}
            shard = _batch_shard_name(list(vectors))
            if not self._atomic_write_shard_unlocked(shard, vectors):
                return
            new_entries: dict[str, dict[str, object]] = {}
            new_dimensionless: dict[str, dict[str, object]] = {}
            for key in prepared:
                index["access_counter"] += 1
                entry = _entry_for(key, index["access_counter"], shard)
                index["entries"][key.digest] = entry
                new_entries[key.digest] = entry
                if dimensionless:
                    alias = _dimensionless_digest(
                        key.normalized_text,
                        key.language,
                        key.provider,
                        key.model,
                        key.task_type,
                        key.preprocessing_identity,
                    )
                    alias_entry = _dimensionless_entry(key)
                    index["dimensionless"][alias] = alias_entry
                    new_dimensionless[alias] = alias_entry
            self._record_change_unlocked(
                index,
                {
                    "op": "put",
                    "access_counter": index["access_counter"],
                    "entries": new_entries,
                    "dimensionless": new_dimensionless,
                },
            )

    def _find_dimensionless(
        self,
        normalized_texts: Sequence[str],
        *,
        language: str,
        provider: str,
        model: str,
        task_type: str,
        preprocessing_identity: str,
    ) -> tuple[int | None, dict[EmbeddingCacheKey, np.ndarray]]:
        language = _normalized_language(language)
        provider = _canonical_provider_id(provider)
        model = _nonempty(model, "model")
        task_type = _nonempty(task_type, "task_type")
        preprocessing_identity = _nonempty(
            preprocessing_identity, "preprocessing_identity"
        )
        texts = tuple(dict.fromkeys(_normalized_text(text) for text in normalized_texts))
        with self._lock:
            index = self._index_unlocked()
            keys: list[EmbeddingCacheKey] = []
            dimensions: int | None = None
            for text in texts:
                alias = _dimensionless_digest(
                    text,
                    language,
                    provider,
                    model,
                    task_type,
                    preprocessing_identity,
                )
                metadata = index["dimensionless"].get(alias)
                if metadata is None:
                    return None, {}
                key = self._key_from_dimensionless(metadata)
                if dimensions is None:
                    dimensions = key.dimensions
                elif key.dimensions != dimensions:
                    return None, {}
                keys.append(key)
            hits = self._get_many_unlocked(tuple(keys), index)
            if len(hits) != len(keys):
                return None, {}
            return dimensions, hits

    def _get_many_unlocked(
        self,
        keys: tuple[EmbeddingCacheKey, ...],
        index: dict[str, object],
    ) -> dict[EmbeddingCacheKey, np.ndarray]:
        entries = index["entries"]
        loaded_shards: dict[str, dict[str, np.ndarray] | None] = {}
        hits: dict[EmbeddingCacheKey, np.ndarray] = {}
        touched: dict[str, int] = {}
        for key in keys:
            metadata = entries.get(key.digest)
            if metadata is None or metadata != _entry_for(
                key, metadata["last_access"], metadata.get("shard")
            ):
                continue
            shard = metadata["shard"]
            if shard not in loaded_shards:
                loaded_shards[shard] = self._load_shard_unlocked(shard)
            vectors = loaded_shards[shard]
            if vectors is None or key.digest not in vectors:
                continue
            try:
                vector = self._prepare_vector(vectors[key.digest], key.dimensions)
            except EmbeddingContractError:
                continue
            index["access_counter"] += 1
            metadata["last_access"] = index["access_counter"]
            vector.setflags(write=False)
            hits[key] = vector
            touched[key.digest] = index["access_counter"]
        if touched:
            self._record_change_unlocked(
                index,
                {"op": "touch", "access_counter": index["access_counter"], "touched": touched},
            )
        return hits

    @staticmethod
    def _prepare_vector(vector: object, dimensions: int) -> np.ndarray:
        if not isinstance(vector, np.ndarray):
            raise EmbeddingContractError("cached vector must be an ndarray")
        if vector.dtype != np.dtype(np.float32):
            raise EmbeddingContractError("cached vector must use float32")
        if vector.ndim != 1 or vector.shape != (dimensions,):
            raise EmbeddingContractError("cached vector has the wrong dimensions")
        copied = np.array(vector, dtype=np.float32, order="C", copy=True)
        if not np.isfinite(copied).all():
            raise EmbeddingContractError("cached vector contains NaN or Inf")
        norm = float(np.linalg.norm(copied))
        if not np.isfinite(norm) or norm <= np.finfo(np.float32).eps:
            raise EmbeddingContractError("cached vector is zero or near-zero")
        copied.setflags(write=False)
        return copied

    @staticmethod
    def _reject_constant(_value: object) -> object:
        raise ValueError

    def _read_base_unlocked(self) -> tuple[dict[str, object], bool]:
        """Возвращает (индекс, повреждён): отсутствующий файл — не повреждение."""
        try:
            text = self.index_path.read_text(encoding="utf-8")
        except OSError:
            return _empty_index(), False
        try:
            raw = json.loads(text, parse_constant=self._reject_constant)
            return self._validate_index(raw), False
        except (UnicodeError, ValueError, TypeError, KeyError, EmbeddingContractError):
            return _empty_index(), True

    def _read_journal_unlocked(self) -> list[dict[str, object]]:
        try:
            text = self.journal_path.read_text(encoding="utf-8")
        except OSError:
            return []
        except UnicodeError:
            return []
        records: list[dict[str, object]] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line, parse_constant=self._reject_constant)
            except (ValueError, TypeError):
                break  # оборванная запись: хвост журнала игнорируем
            if not isinstance(record, dict):
                break
            records.append(record)
        return records

    @staticmethod
    def _apply_journal_record(index: dict[str, object], record: Mapping[str, object]) -> None:
        operation = record.get("op")
        if operation == "put":
            entries = record.get("entries")
            dimensionless = record.get("dimensionless", {})
            if not isinstance(entries, dict) or not isinstance(dimensionless, dict):
                raise ValueError
            index["entries"].update(entries)
            index["dimensionless"].update(dimensionless)
        elif operation == "touch":
            touched = record.get("touched")
            if not isinstance(touched, dict):
                raise ValueError
            for digest, last_access in touched.items():
                metadata = index["entries"].get(digest)
                if metadata is not None:
                    metadata["last_access"] = last_access
        else:
            raise ValueError
        counter = record.get("access_counter")
        if isinstance(counter, bool) or not isinstance(counter, int) or counter < 0:
            raise ValueError
        index["access_counter"] = max(index["access_counter"], counter)

    def _load_index_unlocked(self) -> dict[str, object]:
        """index.json плюс проигранный поверх него журнал."""
        state = self._state
        index, corrupt = self._read_base_unlocked()
        state.journal_lines = 0
        state.needs_compaction = corrupt
        if corrupt:
            # Повреждённый базовый файл: кэш считается пустым целиком, журнал не доверяем.
            return index
        records = self._read_journal_unlocked()
        if not records:
            return index
        try:
            for record in records:
                self._apply_journal_record(index, record)
            index = self._validate_index(index)
        except (ValueError, TypeError, KeyError, EmbeddingContractError):
            state.needs_compaction = True
            return _empty_index()
        state.journal_lines = len(records)
        return index

    @classmethod
    def _validate_index(cls, raw: object) -> dict[str, object]:
        if not isinstance(raw, dict) or set(raw) != {
            "schema_version",
            "access_counter",
            "entries",
            "dimensionless",
        }:
            raise ValueError
        if raw["schema_version"] != _INDEX_SCHEMA_VERSION:
            raise ValueError
        counter = raw["access_counter"]
        if isinstance(counter, bool) or not isinstance(counter, int) or counter < 0:
            raise ValueError
        entries = raw["entries"]
        dimensionless = raw["dimensionless"]
        if not isinstance(entries, dict) or not isinstance(dimensionless, dict):
            raise ValueError
        maximum_access = 0
        for digest, metadata in entries.items():
            if not isinstance(digest, str) or not _SHA256_HEX.fullmatch(digest):
                raise ValueError
            if not isinstance(metadata, dict) or set(metadata) != _ENTRY_FIELDS:
                raise ValueError
            key = cls._key_from_entry(metadata)
            last_access = metadata["last_access"]
            if (
                isinstance(last_access, bool)
                or not isinstance(last_access, int)
                or last_access < 0
                or not isinstance(metadata["shard"], str)
                or not _SHARD_NAME.fullmatch(metadata["shard"])
                or key.digest != digest
            ):
                raise ValueError
            maximum_access = max(maximum_access, last_access)
        if counter < maximum_access:
            raise ValueError
        for alias, metadata in dimensionless.items():
            if not isinstance(alias, str) or not _SHA256_HEX.fullmatch(alias):
                raise ValueError
            if not isinstance(metadata, dict) or set(metadata) != _DIMENSIONLESS_FIELDS:
                raise ValueError
            key = cls._key_from_dimensionless(metadata)
            expected = _dimensionless_digest(
                key.normalized_text,
                key.language,
                key.provider,
                key.model,
                key.task_type,
                key.preprocessing_identity,
            )
            if expected != alias:
                raise ValueError
        return raw

    @staticmethod
    def _key_from_entry(metadata: Mapping[str, object]) -> EmbeddingCacheKey:
        return EmbeddingCacheKey(
            normalized_text=metadata["normalized_text"],
            language=metadata["language"],
            provider=metadata["provider"],
            model=metadata["model"],
            dimensions=metadata["dimensions"],
            task_type=metadata["task_type"],
            preprocessing_identity=metadata["preprocessing_identity"],
        )

    @staticmethod
    def _key_from_dimensionless(metadata: Mapping[str, object]) -> EmbeddingCacheKey:
        return EmbeddingCacheKey(
            normalized_text=metadata["normalized_text"],
            language=metadata["language"],
            provider=metadata["provider"],
            model=metadata["model"],
            dimensions=metadata["dimensions"],
            task_type=metadata["task_type"],
            preprocessing_identity=metadata["preprocessing_identity"],
        )

    def _load_shard_unlocked(self, shard: str) -> dict[str, np.ndarray] | None:
        path = self.root / f"{shard}.npz"
        try:
            vectors: dict[str, np.ndarray] = {}
            with path.open("rb") as stream:
                with np.load(stream, allow_pickle=False) as archive:
                    for digest in archive.files:
                        if not _SHA256_HEX.fullmatch(digest):
                            raise ValueError
                        vector = archive[digest]
                        if (
                            vector.dtype != np.dtype(np.float32)
                            or vector.ndim != 1
                            or not np.isfinite(vector).all()
                        ):
                            raise ValueError
                        copied = np.array(vector, dtype=np.float32, order="C", copy=True)
                        copied.setflags(write=False)
                        vectors[digest] = copied
            return vectors
        except (
            OSError,
            EOFError,
            ValueError,
            TypeError,
            UnicodeError,
            zipfile.BadZipFile,
        ):
            return None

    def _atomic_write_json_unlocked(self, index: Mapping[str, object]) -> bool:
        temporary: Path | None = None
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            descriptor, name = tempfile.mkstemp(
                prefix=".index.", suffix=".tmp", dir=self.root
            )
            temporary = Path(name)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(
                    index,
                    stream,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.index_path)
            return True
        except (OSError, TypeError, ValueError):
            return False
        finally:
            if temporary is not None:
                self._unlink_unlocked(temporary)

    def _atomic_write_shard_unlocked(
        self, shard: str, vectors: Mapping[str, np.ndarray]
    ) -> bool:
        destination = self.root / f"{shard}.npz"
        temporary: Path | None = None
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            descriptor, name = tempfile.mkstemp(
                prefix=f".{shard}.", suffix=".tmp", dir=self.root
            )
            temporary = Path(name)
            with os.fdopen(descriptor, "wb") as stream:
                np.savez_compressed(stream, **vectors)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
            return True
        except (OSError, TypeError, ValueError):
            return False
        finally:
            if temporary is not None:
                self._unlink_unlocked(temporary)

    @staticmethod
    def _unlink_unlocked(path: Path) -> None:
        try:
            path.unlink()
        except OSError:
            pass


class CachedEmbeddingProvider:
    """Deduplicate misses around an async provider without holding cache locks."""

    def __init__(
        self,
        upstream: EmbeddingProvider,
        cache: EmbeddingCache,
        *,
        preprocessing_identity: str | None = None,
        preprocessing_version: str | None = None,
    ) -> None:
        if not callable(getattr(upstream, "embed", None)):
            raise EmbeddingContractError("upstream must provide async embedding")
        if not isinstance(cache, EmbeddingCache):
            raise TypeError("cache must be an EmbeddingCache")
        if preprocessing_identity is not None and preprocessing_version is not None:
            raise EmbeddingContractError(
                "configure only one preprocessing identity value"
            )
        selected_identity = (
            preprocessing_identity
            if preprocessing_identity is not None
            else preprocessing_version
        )
        self._upstream = upstream
        self._cache = cache
        self._provider_id = _canonical_provider_id(getattr(upstream, "name", None))
        self._preprocessing_identity = _nonempty(
            selected_identity, "preprocessing_identity"
        )
        self.name = self._provider_id

    async def embed(self, request: EmbeddingRequest) -> EmbeddingBatch:
        if not isinstance(request, EmbeddingRequest):
            raise EmbeddingContractError("request must be an EmbeddingRequest")
        normalized = tuple(_normalized_text(text) for text in request.texts)
        first_text: dict[str, str] = {}
        for normalized_text, original_text in zip(normalized, request.texts, strict=True):
            first_text.setdefault(normalized_text, original_text)

        effective_dimensions = request.dimensions
        hits: dict[EmbeddingCacheKey, np.ndarray]
        if effective_dimensions is None:
            effective_dimensions, hits = self._cache._find_dimensionless(
                tuple(first_text),
                language=request.language,
                provider=self._provider_id,
                model=request.model,
                task_type=request.task_type,
                preprocessing_identity=self._preprocessing_identity,
            )
            if effective_dimensions is None:
                hits = {}
        keys = self._keys_for_texts(
            tuple(first_text), request, effective_dimensions
        ) if effective_dimensions is not None else {}
        if request.dimensions is not None:
            hits = self._cache.get_many(tuple(keys.values()))

        missing_texts = tuple(text for text, key in keys.items() if key not in hits)
        if request.dimensions is None and not keys:
            missing_texts = tuple(first_text)
        if not missing_texts:
            return self._validated_result(request, normalized, keys, hits, None)

        missing_request = EmbeddingRequest(
            texts=tuple(first_text[text] for text in missing_texts),
            language=request.language,
            model=request.model,
            dimensions=request.dimensions,
            task_type=request.task_type,
        )
        upstream_batch = await self._upstream.embed(missing_request)
        self._validate_upstream_metadata(upstream_batch, request, len(missing_texts))
        effective_dimensions = upstream_batch.dimensions
        keys = self._keys_for_texts(tuple(first_text), request, effective_dimensions)
        if request.dimensions is None:
            hits = {}

        result = self._validated_result(
            request,
            normalized,
            keys,
            hits,
            (missing_texts, upstream_batch),
        )
        new_values = {
            keys[text]: np.array(
                upstream_batch.vectors[index],
                dtype=np.float32,
                order="C",
                copy=True,
            )
            for index, text in enumerate(missing_texts)
        }
        self._cache._put_many(
            new_values,
            dimensionless=request.dimensions is None,
        )
        return result

    def _keys_for_texts(
        self,
        texts: Sequence[str],
        request: EmbeddingRequest,
        dimensions: int | None,
    ) -> dict[str, EmbeddingCacheKey]:
        if dimensions is None:
            return {}
        return {
            text: EmbeddingCacheKey(
                normalized_text=text,
                language=request.language,
                provider=self._provider_id,
                model=request.model,
                dimensions=dimensions,
                task_type=request.task_type,
                preprocessing_identity=self._preprocessing_identity,
            )
            for text in texts
        }

    def _validate_upstream_metadata(
        self,
        batch: object,
        request: EmbeddingRequest,
        expected_rows: int,
    ) -> None:
        if not isinstance(batch, EmbeddingBatch):
            raise EmbeddingContractError("embedding batch must be an EmbeddingBatch")
        if _canonical_provider_id(batch.provider) != self._provider_id:
            raise EmbeddingContractError("embedding batch provider does not match upstream")
        if batch.model != request.model:
            raise EmbeddingContractError("embedding batch model does not match request")
        if request.dimensions is not None and batch.dimensions != request.dimensions:
            raise EmbeddingContractError("embedding batch dimensions do not match request")
        if batch.vectors.shape[0] != expected_rows:
            raise EmbeddingContractError("unexpected embedding batch shape")

    def _validated_result(
        self,
        request: EmbeddingRequest,
        normalized: tuple[str, ...],
        keys: Mapping[str, EmbeddingCacheKey],
        hits: Mapping[EmbeddingCacheKey, np.ndarray],
        miss: tuple[tuple[str, ...], EmbeddingBatch] | None,
    ) -> EmbeddingBatch:
        dimensions = next(iter(keys.values())).dimensions
        if miss is None:
            dtype = np.dtype(np.float32)
            missing_rows: dict[str, np.ndarray] = {}
        else:
            missing_texts, batch = miss
            dtype = batch.vectors.dtype
            missing_rows = {
                text: batch.vectors[index]
                for index, text in enumerate(missing_texts)
            }
        vectors = np.empty((len(normalized), dimensions), dtype=dtype, order="C")
        for index, text in enumerate(normalized):
            key = keys[text]
            source = hits.get(key)
            if source is None:
                source = missing_rows[text]
            vectors[index] = source
        return validate_and_normalize_batch(
            EmbeddingBatch(
                vectors=vectors,
                provider=self._provider_id,
                model=request.model,
                dimensions=dimensions,
            ),
            expected_rows=len(normalized),
        )
