"""EmbeddingCache: запись на диск не должна расти с размером уже накопленного кэша.

Ожидаемая стоимость put_many: один шард пачки + одна строка журнала (два fsync),
без переписывания index.json; попадания в кэш не переписывают index.json.

Регрессия аудита perf:disk-writes/1: каждый put_many переписывал и fsync'ил весь
index.json (миллионы байт к середине книги) и до десятков npz-шардов, а get_many
переписывал индекс даже на попадании ради last_access. На книге в 300 глав это
17 586 fsync и ~2,5 минуты чистого housekeeping, с квадратичным ростом.
"""
import os

import numpy as np
import pytest

from gemini_translator.qa.embeddings.cache import EmbeddingCache, EmbeddingCacheKey

DIMENSIONS = 8


def _key(number: int) -> EmbeddingCacheKey:
    return EmbeddingCacheKey(
        normalized_text=f"предложение номер {number}",
        language="ru",
        provider="gemini",
        model="text-embedding-test",
        dimensions=DIMENSIONS,
        task_type="semantic_similarity",
        preprocessing_identity="p1",
    )


def _vector(number: int) -> np.ndarray:
    rng = np.random.default_rng(number)
    vector = rng.random(DIMENSIONS, dtype=np.float32) + 0.1
    return (vector / np.linalg.norm(vector)).astype(np.float32)


@pytest.fixture
def disk_spy(monkeypatch):
    calls = {"fsync": 0, "index_writes": 0}
    real_replace = os.replace

    def fsync(descriptor):
        calls["fsync"] += 1

    def replace(source, destination):
        if os.path.basename(str(destination)) == "index.json":
            calls["index_writes"] += 1
        return real_replace(source, destination)

    monkeypatch.setattr(os, "fsync", fsync)
    monkeypatch.setattr(os, "replace", replace)
    return calls


def test_put_many_does_not_rewrite_index_per_call(tmp_path, disk_spy):
    cache = EmbeddingCache(tmp_path / "emb")
    expected = {}
    for chapter in range(30):
        batch = {_key(chapter * 8 + j): _vector(chapter * 8 + j) for j in range(8)}
        expected.update(batch)
        cache.put_many(batch)

    assert disk_spy["index_writes"] <= 1, "индекс переписывается на каждом put_many"
    assert disk_spy["fsync"] <= 2 * 30 + 2, f"слишком много fsync на 30 put_many: {disk_spy['fsync']}"

    cache.flush()
    fresh = EmbeddingCache(tmp_path / "emb")
    hits = fresh.get_many(list(expected))
    assert set(hits) == set(expected)
    for key, vector in expected.items():
        np.testing.assert_array_equal(hits[key], vector)


def test_read_hits_do_not_rewrite_index(tmp_path, disk_spy):
    cache = EmbeddingCache(tmp_path / "emb")
    batch = {_key(n): _vector(n) for n in range(16)}
    cache.put_many(batch)
    cache.flush()
    writes_after_flush = disk_spy["index_writes"]

    fresh = EmbeddingCache(tmp_path / "emb")
    for _ in range(5):
        hits = fresh.get_many(list(batch))
        assert len(hits) == 16
    assert disk_spy["index_writes"] == writes_after_flush, "попадание в кэш не должно переписывать индекс"
