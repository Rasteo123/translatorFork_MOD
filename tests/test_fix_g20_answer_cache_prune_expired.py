"""Регресс на qa-b/bugs/3-qa-disk-caches-never-pruned для QaAnswerCache.

До фикса: просроченная по TTL запись при чтении через get() возвращает None,
но файл с ней остаётся на диске навсегда — каталог кэша растёт без ограничения.
После фикса: get() физически удаляет файл просроченной записи, как только
обнаруживает просрочку, так что мёртвые байты не переживают TTL.
"""

from __future__ import annotations

from pathlib import Path

from gemini_translator.qa.llm.answer_cache import QaAnswerCache


def test_expired_answer_is_removed_from_disk_on_read(tmp_path: Path) -> None:
    """Просроченная запись должна не только промахиваться, но и удаляться физически."""
    cache = QaAnswerCache(tmp_path, ttl_seconds=0.0001)
    digest = "a" * 64

    cache.put(digest, {"issues": []})
    stored_path = cache._path(digest)
    assert stored_path.exists()

    import time

    time.sleep(0.01)

    assert cache.get(digest) is None
    assert not stored_path.exists(), "просроченный файл кэша должен удаляться на чтении"


def test_fresh_answer_is_not_removed_from_disk(tmp_path: Path) -> None:
    """Свежая запись не должна затрагиваться чисткой при чтении."""
    cache = QaAnswerCache(tmp_path, ttl_seconds=3600)
    digest = "b" * 64

    cache.put(digest, {"issues": []})
    stored_path = cache._path(digest)

    assert cache.get(digest) == {"issues": []}
    assert stored_path.exists()
