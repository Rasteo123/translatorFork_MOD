"""Behavioral contracts for the disposable content-addressed embedding cache."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

from gemini_translator.qa.capabilities import QaCapabilitySettings
from gemini_translator.qa.embeddings import (
    CachedEmbeddingProvider,
    EmbeddingBatch,
    EmbeddingCache,
    EmbeddingCacheKey,
    EmbeddingContractError,
    EmbeddingRequest,
)
from gemini_translator.qa.embeddings import cache as cache_module
from gemini_translator.qa.semantic_units import SemanticUnitExtractor
from gemini_translator.utils.project_manager import TranslationProjectManager


RAZDEL_IDENTITY = SemanticUnitExtractor(
    QaCapabilitySettings(razdel_enabled=True)
).cache_identity("ru")
LEGACY_IDENTITY = SemanticUnitExtractor(
    QaCapabilitySettings(razdel_enabled=False)
).cache_identity("ru")


def _request(
    *texts: str,
    model: str = "embedding-v1",
    dimensions: int | None = 4,
    task_type: str = "semantic-similarity",
    language: str = "ru",
) -> EmbeddingRequest:
    return EmbeddingRequest(
        texts=texts,
        language=language,
        model=model,
        dimensions=dimensions,
        task_type=task_type,
    )


def _raw_vectors(texts: tuple[str, ...], dimensions: int) -> np.ndarray:
    rows = []
    for text in texts:
        seed = sum(ord(character) for character in text) % 19 + 1
        rows.append(np.arange(seed, seed + dimensions, dtype=np.float64))
    return np.asarray(rows, dtype=np.float64, order="F")


class _CountingProvider:
    name = "counting"

    def __init__(self, *, effective_dimensions: int = 4):
        self.effective_dimensions = effective_dimensions
        self.requests: list[EmbeddingRequest] = []
        self.raw_arrays: list[np.ndarray] = []

    async def embed(self, request: EmbeddingRequest) -> EmbeddingBatch:
        self.requests.append(request)
        dimensions = request.dimensions or self.effective_dimensions
        vectors = _raw_vectors(request.texts, dimensions)
        self.raw_arrays.append(vectors)
        return EmbeddingBatch(
            vectors=vectors,
            provider=self.name,
            model=request.model,
            dimensions=dimensions,
        )


def _provider(upstream, root: Path, identity: str = RAZDEL_IDENTITY):
    return CachedEmbeddingProvider(
        upstream,
        EmbeddingCache(root),
        preprocessing_identity=identity,
    )


def _key(
    text: str,
    *,
    language: str = "ru",
    provider: str = "counting",
    model: str = "embedding-v1",
    dimensions: int = 4,
    task_type: str = "semantic-similarity",
    preprocessing_identity: str = RAZDEL_IDENTITY,
) -> EmbeddingCacheKey:
    return EmbeddingCacheKey.from_text(
        text,
        language=language,
        provider=provider,
        model=model,
        dimensions=dimensions,
        task_type=task_type,
        preprocessing_identity=preprocessing_identity,
    )


def test_full_and_partial_hits_only_embed_unique_missing_texts_in_first_seen_order(tmp_path):
    """Refetching hits or duplicate misses would waste network calls and reorder provider input."""
    upstream = _CountingProvider()
    provider = _provider(upstream, tmp_path / "cache")

    first = asyncio.run(provider.embed(_request("один", "два")))
    repeated = asyncio.run(provider.embed(_request("один", "два")))
    partial = asyncio.run(provider.embed(_request("два", "три", "три", "четыре", "два")))

    assert [request.texts for request in upstream.requests] == [
        ("один", "два"),
        ("три", "четыре"),
    ]
    np.testing.assert_array_equal(repeated.vectors, first.vectors)
    np.testing.assert_array_equal(partial.vectors[0], first.vectors[1])
    np.testing.assert_array_equal(partial.vectors[1], partial.vectors[2])
    np.testing.assert_array_equal(partial.vectors[0], partial.vectors[4])
    assert partial.vectors.dtype == np.float32
    assert partial.vectors.flags.c_contiguous
    assert not partial.vectors.flags.writeable


def test_cache_identity_normalizes_text_and_invalidates_every_embedding_space_field(tmp_path):
    """Omitting any identity field can return a vector from a different semantic space."""
    cache_root = tmp_path / "cache"
    upstream = _CountingProvider()

    asyncio.run(_provider(upstream, cache_root).embed(_request("  ТеКст\tодин  ")))
    asyncio.run(_provider(upstream, cache_root).embed(_request("текст один")))
    asyncio.run(_provider(upstream, cache_root).embed(_request("другой текст")))
    asyncio.run(_provider(upstream, cache_root).embed(_request("текст один", model="embedding-v2")))
    asyncio.run(_provider(upstream, cache_root).embed(_request("текст один", dimensions=3)))
    asyncio.run(
        _provider(upstream, cache_root).embed(
            _request("текст один", task_type="retrieval-document")
        )
    )
    asyncio.run(_provider(upstream, cache_root, LEGACY_IDENTITY).embed(_request("текст один")))

    other_upstream = _CountingProvider()
    other_upstream.name = "other_provider"
    asyncio.run(_provider(other_upstream, cache_root).embed(_request("текст один")))

    assert len(upstream.requests) == 6
    assert len(other_upstream.requests) == 1
    assert SemanticUnitExtractor.PREPROCESSING_VERSION in RAZDEL_IDENTITY
    assert RAZDEL_IDENTITY != LEGACY_IDENTITY


def test_cache_key_is_frozen_typed_and_segmenter_identity_changes_its_sha256():
    """A mutable or segmenter-blind key could change after insertion or reuse stale vectors."""
    razdel_key = _key("Текст", preprocessing_identity=RAZDEL_IDENTITY)
    legacy_key = _key("Текст", preprocessing_identity=LEGACY_IDENTITY)

    assert len(razdel_key.digest) == 64
    assert razdel_key.digest != legacy_key.digest
    with pytest.raises(FrozenInstanceError):
        razdel_key.model = "other"
    with pytest.raises((EmbeddingContractError, TypeError, ValueError)):
        EmbeddingCacheKey.from_text(
            "text",
            language="ru",
            provider="counting",
            model="m",
            dimensions=True,
            task_type="semantic-similarity",
            preprocessing_identity="",
        )
    with pytest.raises(TypeError, match="language"):
        EmbeddingCacheKey.from_text(
            "text",
            provider="counting",
            model="m",
            dimensions=4,
            task_type="semantic-similarity",
            preprocessing_identity=RAZDEL_IDENTITY,
        )


def test_cached_provider_requires_effective_identity_and_accepts_plan_keyword_alias(tmp_path):
    """A default identity can silently reuse vectors after callers select another segmenter."""
    upstream = _CountingProvider()
    legacy_keyword_provider = CachedEmbeddingProvider(
        upstream,
        EmbeddingCache(tmp_path / "legacy-keyword"),
        preprocessing_version=LEGACY_IDENTITY,
    )

    asyncio.run(legacy_keyword_provider.embed(_request("legacy")))

    assert [request.texts for request in upstream.requests] == [("legacy",)]
    with pytest.raises(EmbeddingContractError):
        CachedEmbeddingProvider(upstream, EmbeddingCache(tmp_path / "missing"))
    with pytest.raises(EmbeddingContractError):
        CachedEmbeddingProvider(
            upstream,
            EmbeddingCache(tmp_path / "ambiguous"),
            preprocessing_identity=RAZDEL_IDENTITY,
            preprocessing_version=LEGACY_IDENTITY,
        )


@pytest.mark.parametrize(
    "damage", ["index", "shard", "truncated_zip", "object_shard"]
)
def test_corrupted_or_pickle_requiring_cache_data_is_a_miss_not_an_exception(tmp_path, damage):
    """Treating disposable cache corruption as fatal would abort QA or enable pickle loading."""
    root = tmp_path / "cache"
    upstream = _CountingProvider()
    provider = _provider(upstream, root)
    asyncio.run(provider.embed(_request("один")))

    if damage == "index":
        (root / "index.json").write_text("{broken", encoding="utf-8")
    else:
        shard = next(root.glob("*.npz"))
        if damage == "shard":
            shard.write_bytes(b"not an npz")
        elif damage == "truncated_zip":
            shard.write_bytes(shard.read_bytes()[:-22])
        else:
            with shard.open("wb") as stream:
                np.savez(stream, **{_key("один").digest: np.array([object()], dtype=object)})

    result = asyncio.run(provider.embed(_request("один")))

    assert len(upstream.requests) == 2
    assert result.vectors.shape == (1, 4)
    assert np.isfinite(result.vectors).all()


def test_atomic_replace_failure_keeps_previous_hits_and_cleans_temp_files(tmp_path, monkeypatch):
    """A failed atomic cache write must not destroy an earlier readable entry."""
    root = tmp_path / "cache"
    upstream = _CountingProvider()
    provider = _provider(upstream, root)
    old = asyncio.run(provider.embed(_request("старый")))
    real_replace = cache_module.os.replace

    with monkeypatch.context() as patch:
        patch.setattr(cache_module.os, "replace", lambda _source, _target: (_ for _ in ()).throw(OSError()))
        fresh = asyncio.run(provider.embed(_request("новый")))
        assert fresh.vectors.shape == (1, 4)

    old_again = asyncio.run(provider.embed(_request("старый")))
    asyncio.run(provider.embed(_request("новый")))

    assert real_replace is not None
    np.testing.assert_array_equal(old_again.vectors, old.vectors)
    assert [request.texts for request in upstream.requests] == [
        ("старый",),
        ("новый",),
        ("новый",),
    ]
    assert not list(root.glob("*.tmp"))


def test_unwritable_cache_root_does_not_hide_a_valid_upstream_result(tmp_path):
    """A disposable cache write failure must not turn a successful provider call into QA failure."""
    root = tmp_path / "cache"
    root.write_text("occupied by a file", encoding="utf-8")
    upstream = _CountingProvider()

    result = asyncio.run(_provider(upstream, root).embed(_request("still works")))

    assert result.vectors.shape == (1, 4)
    assert len(upstream.requests) == 1
    assert root.read_text(encoding="utf-8") == "occupied by a file"


def test_project_manager_places_embedding_cache_in_dedicated_project_subdirectory(tmp_path):
    """Using a shared or history directory would make cache pruning unsafe."""
    manager = TranslationProjectManager(str(tmp_path))

    assert manager.get_translation_qa_embedding_cache_dir() == (
        tmp_path / "translation_qa_embedding_cache"
    )


def test_dimensions_none_records_effective_dimension_and_subsequent_identical_request_hits(tmp_path):
    """Guessing a dimension before the first response can collide with explicitly sized vectors."""
    root = tmp_path / "cache"
    upstream = _CountingProvider(effective_dimensions=5)
    provider = _provider(upstream, root)
    request = _request("один", "два", dimensions=None)

    first = asyncio.run(provider.embed(request))
    asyncio.run(_provider(upstream, root).embed(_request("один", "два", dimensions=3)))
    second = asyncio.run(provider.embed(request))

    assert [item.dimensions for item in upstream.requests] == [None, 3]
    assert first.dimensions == second.dimensions == 5
    np.testing.assert_array_equal(first.vectors, second.vectors)


def test_dimensionless_partial_lookup_refetches_full_original_request_without_distortion(tmp_path):
    """Inferring dimensions from only part of a batch can combine incompatible vector spaces."""
    root = tmp_path / "cache"
    upstream = _CountingProvider(effective_dimensions=6)
    provider = _provider(upstream, root)
    asyncio.run(provider.embed(_request("known", dimensions=None, language="en")))

    result = asyncio.run(
        provider.embed(_request("known", "new", dimensions=None, language="en-US"))
    )

    assert upstream.requests[-1] == _request("known", "new", dimensions=None, language="en-US")
    assert result.dimensions == 6


def test_cancellation_propagates_and_does_not_create_cache_entry(tmp_path):
    """Swallowing cancellation would prevent callers from stopping semantic QA promptly."""
    class _CancelledProvider(_CountingProvider):
        async def embed(self, request):
            self.requests.append(request)
            raise asyncio.CancelledError()

    root = tmp_path / "cache"
    cancelled = _CancelledProvider()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(_provider(cancelled, root).embed(_request("stop")))

    healthy = _CountingProvider()
    asyncio.run(_provider(healthy, root).embed(_request("stop")))
    assert len(healthy.requests) == 1


@pytest.mark.parametrize(
    "malformation",
    ["provider", "model", "dimensions", "rows", "nan"],
)
def test_upstream_metadata_and_schema_are_validated_before_caching(tmp_path, malformation):
    """Caching malformed provider output would persist cross-space or invalid vectors."""
    class _MalformedProvider(_CountingProvider):
        async def embed(self, request):
            self.requests.append(request)
            provider = "other_provider" if malformation == "provider" else self.name
            model = "other-model" if malformation == "model" else request.model
            dimensions = 3 if malformation == "dimensions" else 4
            rows = len(request.texts) - 1 if malformation == "rows" else len(request.texts)
            vectors = np.ones((rows, dimensions), dtype=np.float64)
            if malformation == "nan":
                vectors[0, 0] = np.nan
            return EmbeddingBatch(
                vectors=vectors,
                provider=provider,
                model=model,
                dimensions=dimensions,
            )

    root = tmp_path / "cache"
    with pytest.raises(EmbeddingContractError):
        asyncio.run(_provider(_MalformedProvider(), root).embed(_request("one", "two")))

    assert EmbeddingCache(root).get_many((_key("one"), _key("two"))) == {}


def test_partial_batch_is_validated_once_at_the_reconstructed_full_batch_boundary(tmp_path, monkeypatch):
    """Normalizing only a miss sub-batch can skip validation of cached and reconstructed rows."""
    root = tmp_path / "cache"
    upstream = _CountingProvider()
    provider = _provider(upstream, root)
    asyncio.run(provider.embed(_request("cached")))
    calls: list[int] = []
    real_validate = cache_module.validate_and_normalize_batch

    def recording_validate(batch, expected_rows):
        calls.append(expected_rows)
        return real_validate(batch, expected_rows)

    monkeypatch.setattr(cache_module, "validate_and_normalize_batch", recording_validate)
    raw_before = len(upstream.raw_arrays)
    result = asyncio.run(provider.embed(_request("cached", "new", "new")))

    assert calls == [3]
    assert len(upstream.raw_arrays) == raw_before + 1
    assert upstream.requests[-1].texts == ("new",)
    assert upstream.raw_arrays[-1].flags.writeable
    assert not np.shares_memory(result.vectors, upstream.raw_arrays[-1])


def test_two_cache_instances_do_not_lose_concurrent_shard_updates(tmp_path):
    """Instance-local locking alone can lose read-modify-write updates in one process."""
    root = tmp_path / "cache"
    keys = [_key(f"concurrent-{index}") for index in range(24)]

    def write(index: int) -> None:
        cache = EmbeddingCache(root)
        cache.put_many(
            {keys[index]: np.array([1.0, index + 1.0, 2.0, 3.0], dtype=np.float32)}
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(write, range(len(keys))))

    assert set(EmbeddingCache(root).get_many(keys)) == set(keys)


def test_cache_module_import_does_not_load_qt_translation_engine_or_network_clients():
    """A local cache import must remain usable in offline non-Qt QA processes."""
    script = """
import json
import sys
import gemini_translator.qa.embeddings.cache
blocked = [name for name in sys.modules if name.startswith(('PyQt', 'aiohttp')) or 'translation_engine' in name]
print(json.dumps(blocked))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(completed.stdout) == []


def test_language_is_part_of_cache_identity_and_normalized_before_lookup(tmp_path):
    """Dropping language can reuse identical text across distinct embedding spaces."""
    root = tmp_path / "cache"
    upstream = _CountingProvider()
    provider = _provider(upstream, root)

    english = asyncio.run(provider.embed(_request("same text", language="en-US")))
    russian = asyncio.run(provider.embed(_request("same text", language="ru_RU")))
    english_again = asyncio.run(provider.embed(_request("same text", language="EN")))

    assert [request.language for request in upstream.requests] == ["en", "ru"]
    np.testing.assert_array_equal(english_again.vectors, english.vectors)
    assert russian.vectors.shape == english.vectors.shape
    assert _key("same text", language="en").digest != _key(
        "same text", language="ru"
    ).digest


def test_dimensionless_aliases_are_isolated_by_normalized_language(tmp_path):
    """A dimensions=None alias from another language must not suppress an upstream call."""
    root = tmp_path / "cache"
    upstream = _CountingProvider(effective_dimensions=5)
    provider = _provider(upstream, root)

    asyncio.run(provider.embed(_request("same text", dimensions=None, language="en")))
    asyncio.run(provider.embed(_request("same text", dimensions=None, language="ru")))
    asyncio.run(provider.embed(_request("same text", dimensions=None, language="en-US")))

    assert [(request.language, request.dimensions) for request in upstream.requests] == [
        ("en", None),
        ("ru", None),
    ]


def test_schema_v1_index_is_a_clean_miss_and_is_replaced_by_v2(tmp_path):
    """A language-blind v1 index must never be interpreted as the current cache schema."""
    root = tmp_path / "cache"
    root.mkdir()
    normalized_text = "same text"
    old_parts = (
        normalized_text,
        "counting",
        "embedding-v1",
        4,
        "semantic-similarity",
        RAZDEL_IDENTITY,
    )
    old_digest = hashlib.sha256(
        json.dumps(old_parts, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
    old_entry = {
        "normalized_text": normalized_text,
        "provider": "counting",
        "model": "embedding-v1",
        "dimensions": 4,
        "task_type": "semantic-similarity",
        "preprocessing_identity": RAZDEL_IDENTITY,
        "shard": old_digest[:2],
        "last_access": 1,
    }
    (root / "index.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "access_counter": 1,
                "entries": {old_digest: old_entry},
                "dimensionless": {},
            }
        ),
        encoding="utf-8",
    )
    with (root / f"{old_digest[:2]}.npz").open("wb") as stream:
        np.savez(stream, **{old_digest: np.ones(4, dtype=np.float32)})
    upstream = _CountingProvider()

    asyncio.run(_provider(upstream, root).embed(_request("same text", language="en")))

    assert [request.language for request in upstream.requests] == ["en"]
    assert json.loads((root / "index.json").read_text(encoding="utf-8"))[
        "schema_version"
    ] == 2
