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


_INDEX_SCHEMA_VERSION = 1
_SAFE_PROVIDER_ID = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_SHA256_HEX = re.compile(r"[0-9a-f]{64}\Z")
_ENTRY_FIELDS = frozenset(
    {
        "normalized_text",
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
    provider: str
    model: str
    dimensions: int
    task_type: str
    preprocessing_identity: str
    digest: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "normalized_text", _normalized_text(self.normalized_text))
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
        provider: str,
        model: str,
        dimensions: int,
        task_type: str,
        preprocessing_identity: str,
    ) -> "EmbeddingCacheKey":
        return cls(
            normalized_text=text,
            provider=provider,
            model=model,
            dimensions=dimensions,
            task_type=task_type,
            preprocessing_identity=preprocessing_identity,
        )


def _dimensionless_digest(
    normalized_text: str,
    provider: str,
    model: str,
    task_type: str,
    preprocessing_identity: str,
) -> str:
    return _identity_digest(
        (normalized_text, provider, model, task_type, preprocessing_identity)
    )


def _empty_index() -> dict[str, object]:
    return {
        "schema_version": _INDEX_SCHEMA_VERSION,
        "access_counter": 0,
        "entries": {},
        "dimensionless": {},
    }


def _entry_for(key: EmbeddingCacheKey, last_access: int) -> dict[str, object]:
    return {
        "normalized_text": key.normalized_text,
        "provider": key.provider,
        "model": key.model,
        "dimensions": key.dimensions,
        "task_type": key.task_type,
        "preprocessing_identity": key.preprocessing_identity,
        "shard": key.digest[:2],
        "last_access": last_access,
    }


def _dimensionless_entry(key: EmbeddingCacheKey) -> dict[str, object]:
    return {
        "normalized_text": key.normalized_text,
        "provider": key.provider,
        "model": key.model,
        "dimensions": key.dimensions,
        "task_type": key.task_type,
        "preprocessing_identity": key.preprocessing_identity,
    }


class EmbeddingCache:
    """Process-safe local cache whose entire root is safe to discard."""

    _locks_guard = threading.Lock()
    _locks: dict[str, threading.RLock] = {}

    def __init__(self, root: str | os.PathLike[str]) -> None:
        if not isinstance(root, (str, os.PathLike)):
            raise TypeError("root must be a filesystem path")
        self.root = Path(root).expanduser().resolve()
        self.index_path = self.root / "index.json"
        lock_key = os.fspath(self.root)
        with self._locks_guard:
            self._lock = self._locks.setdefault(lock_key, threading.RLock())

    def get_many(
        self, keys: Sequence[EmbeddingCacheKey]
    ) -> dict[EmbeddingCacheKey, np.ndarray]:
        unique = self._unique_keys(keys)
        if not unique:
            return {}
        with self._lock:
            index = self._load_index_unlocked()
            return self._get_many_unlocked(unique, index)

    def put_many(self, values: Mapping[EmbeddingCacheKey, np.ndarray]) -> None:
        self._put_many(values, dimensionless=False)

    def prune(self, max_bytes: int) -> int:
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int):
            raise TypeError("max_bytes must be a nonnegative integer")
        if max_bytes < 0:
            raise ValueError("max_bytes must be a nonnegative integer")

        with self._lock:
            before = self._cache_size_unlocked()
            self._cleanup_temps_unlocked()
            index = self._load_index_unlocked()
            entries = index["entries"]
            referenced_shards = {entry["shard"] for entry in entries.values()}
            self._remove_orphan_shards_unlocked(referenced_shards)

            while self._cache_size_unlocked() > max_bytes and entries:
                digest = min(
                    entries,
                    key=lambda item: (entries[item]["last_access"], item),
                )
                shard = entries[digest]["shard"]
                next_index = self._copy_index(index)
                del next_index["entries"][digest]
                next_index["dimensionless"] = {
                    alias: metadata
                    for alias, metadata in next_index["dimensionless"].items()
                    if self._key_from_dimensionless(metadata).digest != digest
                }
                if not self._atomic_write_json_unlocked(next_index):
                    break
                index = next_index
                entries = index["entries"]
                self._rewrite_shard_for_index_unlocked(shard, entries)

            if self._cache_size_unlocked() > max_bytes and not entries:
                self._unlink_unlocked(self.index_path)
                self._remove_orphan_shards_unlocked(set())
                self._cleanup_temps_unlocked()
            after = self._cache_size_unlocked()
            if after > max_bytes:
                raise OSError("embedding cache could not reach the requested byte limit")
            return max(0, before - after)

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
            index = self._load_index_unlocked()
            grouped: dict[str, dict[str, np.ndarray]] = {}
            keys_by_digest: dict[str, EmbeddingCacheKey] = {}
            for key, vector in prepared.items():
                grouped.setdefault(key.digest[:2], {})[key.digest] = vector
                keys_by_digest[key.digest] = key

            written: list[str] = []
            for shard, additions in grouped.items():
                existing = self._load_shard_unlocked(shard)
                merged = {} if existing is None else existing
                merged.update(additions)
                if self._atomic_write_shard_unlocked(shard, merged):
                    written.extend(additions)

            if not written:
                return
            for digest in written:
                key = keys_by_digest[digest]
                index["access_counter"] += 1
                index["entries"][digest] = _entry_for(
                    key, index["access_counter"]
                )
                if dimensionless:
                    alias = _dimensionless_digest(
                        key.normalized_text,
                        key.provider,
                        key.model,
                        key.task_type,
                        key.preprocessing_identity,
                    )
                    index["dimensionless"][alias] = _dimensionless_entry(key)
            self._atomic_write_json_unlocked(index)

    def _find_dimensionless(
        self,
        normalized_texts: Sequence[str],
        *,
        provider: str,
        model: str,
        task_type: str,
        preprocessing_identity: str,
    ) -> tuple[int | None, dict[EmbeddingCacheKey, np.ndarray]]:
        provider = _canonical_provider_id(provider)
        model = _nonempty(model, "model")
        task_type = _nonempty(task_type, "task_type")
        preprocessing_identity = _nonempty(
            preprocessing_identity, "preprocessing_identity"
        )
        texts = tuple(dict.fromkeys(_normalized_text(text) for text in normalized_texts))
        with self._lock:
            index = self._load_index_unlocked()
            keys: list[EmbeddingCacheKey] = []
            dimensions: int | None = None
            for text in texts:
                alias = _dimensionless_digest(
                    text, provider, model, task_type, preprocessing_identity
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
        touched = False
        for key in keys:
            metadata = entries.get(key.digest)
            if metadata is None or metadata != _entry_for(key, metadata["last_access"]):
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
            touched = True
        if touched:
            self._atomic_write_json_unlocked(index)
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

    def _load_index_unlocked(self) -> dict[str, object]:
        try:
            raw = json.loads(
                self.index_path.read_text(encoding="utf-8"),
                parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
            )
            return self._validate_index(raw)
        except (OSError, UnicodeError, ValueError, TypeError, KeyError, EmbeddingContractError):
            return _empty_index()

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
                or metadata["shard"] != digest[:2]
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
    def _copy_index(index: Mapping[str, object]) -> dict[str, object]:
        return {
            "schema_version": index["schema_version"],
            "access_counter": index["access_counter"],
            "entries": {
                digest: dict(metadata)
                for digest, metadata in index["entries"].items()
            },
            "dimensionless": {
                digest: dict(metadata)
                for digest, metadata in index["dimensionless"].items()
            },
        }

    def _rewrite_shard_for_index_unlocked(
        self, shard: str, entries: Mapping[str, Mapping[str, object]]
    ) -> None:
        path = self.root / f"{shard}.npz"
        retained = {
            digest
            for digest, metadata in entries.items()
            if metadata["shard"] == shard
        }
        if not retained:
            self._unlink_unlocked(path)
            return
        vectors = self._load_shard_unlocked(shard)
        if vectors is None:
            self._unlink_unlocked(path)
            return
        self._atomic_write_shard_unlocked(
            shard, {digest: vector for digest, vector in vectors.items() if digest in retained}
        )

    def _remove_orphan_shards_unlocked(self, referenced: set[str]) -> None:
        if not self.root.exists():
            return
        for path in self.root.glob("*.npz"):
            if path.stem not in referenced:
                self._unlink_unlocked(path)

    def _cleanup_temps_unlocked(self) -> None:
        if not self.root.exists():
            return
        for path in self.root.glob(".*.tmp"):
            self._unlink_unlocked(path)

    def _cache_size_unlocked(self) -> int:
        if not self.root.exists():
            return 0
        size = 0
        for path in self.root.iterdir():
            try:
                if path.is_file() or path.is_symlink():
                    size += path.stat(follow_symlinks=False).st_size
            except OSError:
                continue
        return size

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
