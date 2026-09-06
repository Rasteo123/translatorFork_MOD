"""cluster-56: QaAnswerCache и LanguageRuleCache — общая база DiskTtlCache.

До рефакторинга оба класса независимо реализовывали один и тот же TTL-кэш на
диске (TTL-проверка, атомарная запись через .tmp+os.replace, схема путей
root/hash[:2]/hash.json). QaAnswerCache уже нёс фикс qa-b/bugs/3
(просроченная запись физически удаляется при чтении); LanguageRuleCache этот
фикс не получил. Канонический gemini_translator.qa.disk_cache.DiskTtlCache
несёт исправленное поведение один раз, оба класса наследуют его.

(а) Характеризационные тесты на DiskTtlCache — крайние случаи, которые
    исторически различали копии (или могли разойтись).
(б) Тест-маршрутизация: каждый бывший «собственный» путь чтения/записи обязан
    в итоге дойти до DiskTtlCache.get_raw/put_raw. Обязан падать (RED) до
    рефакторинга — до него QaAnswerCache и LanguageRuleCache не знают о
    DiskTtlCache вовсе (импорт падает) — и проходить (GREEN) после.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# (а) Характеризация канонической реализации
# ---------------------------------------------------------------------------


def test_put_then_get_round_trips_raw_payload(tmp_path: Path) -> None:
    from gemini_translator.qa.disk_cache import DiskTtlCache

    cache = DiskTtlCache(tmp_path)
    digest = "a" * 64

    cache.put_raw(digest, {"answer": {"issues": []}})

    payload = cache.get_raw(digest)
    assert payload is not None
    assert payload["answer"] == {"issues": []}
    assert isinstance(payload["stored_at"], (int, float))


def test_write_is_atomic_no_tmp_file_left_behind(tmp_path: Path) -> None:
    from gemini_translator.qa.disk_cache import DiskTtlCache

    cache = DiskTtlCache(tmp_path)
    digest = "b" * 64

    cache.put_raw(digest, {"answer": 1})

    path = cache._path(digest)
    assert path.exists()
    assert not path.with_name(path.name + ".tmp").exists()


def test_expired_entry_is_pruned_from_disk_on_read(tmp_path: Path) -> None:
    """qa-b/bugs/3: просроченная запись должна не только промахиваться, но и
    удаляться физически, иначе каталог кэша растёт без ограничения."""
    from gemini_translator.qa.disk_cache import DiskTtlCache

    cache = DiskTtlCache(tmp_path, ttl_seconds=0.0001)
    digest = "c" * 64
    cache.put_raw(digest, {"answer": 1})
    stored_path = cache._path(digest)
    assert stored_path.exists()

    time.sleep(0.01)

    assert cache.get_raw(digest) is None
    assert not stored_path.exists(), "просроченный файл кэша должен удаляться на чтении"


def test_fresh_entry_is_not_removed_from_disk(tmp_path: Path) -> None:
    from gemini_translator.qa.disk_cache import DiskTtlCache

    cache = DiskTtlCache(tmp_path, ttl_seconds=3600)
    digest = "d" * 64
    cache.put_raw(digest, {"answer": 1})
    stored_path = cache._path(digest)

    assert cache.get_raw(digest) is not None
    assert stored_path.exists()


def test_corrupt_json_is_a_miss_not_a_crash(tmp_path: Path) -> None:
    from gemini_translator.qa.disk_cache import DiskTtlCache

    cache = DiskTtlCache(tmp_path)
    digest = "e" * 64
    path = cache._path(digest)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ broken", encoding="utf-8")

    assert cache.get_raw(digest) is None


def test_missing_or_non_numeric_stored_at_is_a_miss(tmp_path: Path) -> None:
    from gemini_translator.qa.disk_cache import DiskTtlCache

    cache = DiskTtlCache(tmp_path)
    digest = "f" * 64
    path = cache._path(digest)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"answer": 1, "stored_at": "not-a-number"}), encoding="utf-8")

    assert cache.get_raw(digest) is None


def test_nonsense_digest_is_refused_rather_than_writing_anywhere(tmp_path: Path) -> None:
    from gemini_translator.qa.disk_cache import DiskTtlCache

    cache = DiskTtlCache(tmp_path)

    with pytest.raises(ValueError):
        cache._path("../../escape")


def test_path_scheme_is_root_hash_prefix_hash_json(tmp_path: Path) -> None:
    from gemini_translator.qa.disk_cache import DiskTtlCache

    cache = DiskTtlCache(tmp_path)
    digest = "0123456789abcdef" * 4

    path = cache._path(digest)

    assert path == tmp_path / digest[:2] / f"{digest}.json"


# ---------------------------------------------------------------------------
# (б) Маршрутизация: QaAnswerCache и LanguageRuleCache обязаны идти через
#     DiskTtlCache.get_raw/put_raw, а не через собственную копию логики.
# ---------------------------------------------------------------------------


def test_qa_answer_cache_routes_through_disk_ttl_cache(tmp_path, monkeypatch) -> None:
    from gemini_translator.qa.disk_cache import DiskTtlCache
    from gemini_translator.qa.llm.answer_cache import QaAnswerCache

    calls: list[str] = []
    original_get_raw = DiskTtlCache.get_raw
    original_put_raw = DiskTtlCache.put_raw

    def spy_get_raw(self, digest):
        calls.append("get_raw")
        return original_get_raw(self, digest)

    def spy_put_raw(self, digest, fields):
        calls.append("put_raw")
        return original_put_raw(self, digest, fields)

    monkeypatch.setattr(DiskTtlCache, "get_raw", spy_get_raw)
    monkeypatch.setattr(DiskTtlCache, "put_raw", spy_put_raw)

    cache = QaAnswerCache(tmp_path)
    digest = "1" * 64

    cache.put(digest, {"issues": []})
    result = cache.get(digest)

    assert result == {"issues": []}
    assert calls == ["put_raw", "get_raw"], (
        "QaAnswerCache.get/put обязаны идти через DiskTtlCache.get_raw/put_raw"
    )


def test_language_rule_cache_routes_through_disk_ttl_cache(tmp_path, monkeypatch) -> None:
    from gemini_translator.qa.disk_cache import DiskTtlCache
    from gemini_translator.qa.language_rules import (
        LanguageRuleCache,
        LanguageRuleCacheKey,
        LanguageRuleMatch,
        fingerprint_text,
    )

    calls: list[str] = []
    original_get_raw = DiskTtlCache.get_raw
    original_put_raw = DiskTtlCache.put_raw

    def spy_get_raw(self, digest):
        calls.append("get_raw")
        return original_get_raw(self, digest)

    def spy_put_raw(self, digest, fields):
        calls.append("put_raw")
        return original_put_raw(self, digest, fields)

    monkeypatch.setattr(DiskTtlCache, "get_raw", spy_get_raw)
    monkeypatch.setattr(DiskTtlCache, "put_raw", spy_put_raw)

    key = LanguageRuleCacheKey(
        text_fingerprint=fingerprint_text("Он шёл домой."),
        language="ru",
        endpoint="http://127.0.0.1:8081/v2/check",
        server_version="6.6",
    )
    match = LanguageRuleMatch(
        rule_id="MORFOLOGIK_RULE_RU_RU",
        category="TYPOS",
        message="Возможная опечатка",
        unit_id="u-1",
        block_id="b-1",
        unit_start=0,
        unit_end=3,
        matched_text="Он ",
        replacements=("Он",),
    )
    cache = LanguageRuleCache(tmp_path)

    cache.put(key, (match,))
    result = cache.get(key)

    assert result is not None
    assert result[0].rule_id == "MORFOLOGIK_RULE_RU_RU"
    assert calls == ["put_raw", "get_raw"], (
        "LanguageRuleCache.get/put обязаны идти через DiskTtlCache.get_raw/put_raw"
    )


# ---------------------------------------------------------------------------
# Расхождение, которое устраняет рефакторинг: LanguageRuleCache раньше не
# удалял просроченный файл при чтении (в отличие от QaAnswerCache, где это
# было фиксом qa-b/bugs/3). После рефакторинга оба ведут себя одинаково.
# ---------------------------------------------------------------------------


def test_language_rule_cache_also_prunes_expired_entry_from_disk(tmp_path) -> None:
    from gemini_translator.qa.language_rules import (
        LanguageRuleCache,
        LanguageRuleCacheKey,
        LanguageRuleMatch,
        fingerprint_text,
    )

    cache = LanguageRuleCache(tmp_path, ttl_seconds=0.0001)
    key = LanguageRuleCacheKey(
        text_fingerprint=fingerprint_text("Он шёл домой."),
        language="ru",
        endpoint="http://127.0.0.1:8081/v2/check",
        server_version="6.6",
    )
    match = LanguageRuleMatch(
        rule_id="MORFOLOGIK_RULE_RU_RU",
        category="TYPOS",
        message="Возможная опечатка",
        unit_id="u-1",
        block_id="b-1",
        unit_start=0,
        unit_end=3,
        matched_text="Он ",
        replacements=("Он",),
    )
    cache.put(key, (match,))
    stored_path = cache._path(key.digest())
    assert stored_path.exists()

    time.sleep(0.01)

    assert cache.get(key) is None
    assert not stored_path.exists(), "просроченный файл LanguageRuleCache тоже должен удаляться"
