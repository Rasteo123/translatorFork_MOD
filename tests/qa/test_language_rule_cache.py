"""A cached rule answer must expire with everything that could change it."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from gemini_translator.qa.capabilities import QaCapabilitySettings
from gemini_translator.qa.language_rules import (
    LanguageRuleCache,
    LanguageRuleCacheKey,
    LanguageRuleMatch,
    LanguageRuleService,
    fingerprint_text,
)
from gemini_translator.qa.models import SemanticInlineSpan, SemanticUnit


def _key(**overrides) -> LanguageRuleCacheKey:
    values = {
        "text_fingerprint": fingerprint_text("Он шёл домой."),
        "language": "ru",
        "endpoint": "http://127.0.0.1:8081/v2/check",
        "server_version": "6.6",
        "disabled_rule_ids": ("RU_UPPERCASE",),
        "preprocessing_version": "semantic-units-v1",
    }
    values.update(overrides)
    return LanguageRuleCacheKey(**values)  # type: ignore[arg-type]


def _match(**overrides) -> LanguageRuleMatch:
    values = {
        "rule_id": "MORFOLOGIK_RULE_RU_RU",
        "category": "TYPOS",
        "message": "Возможная опечатка",
        "unit_id": "u-1",
        "block_id": "b-1",
        "unit_start": 0,
        "unit_end": 3,
        "matched_text": "Он ",
        "replacements": ("Он",),
    }
    values.update(overrides)
    return LanguageRuleMatch(**values)  # type: ignore[arg-type]


def _unit(text: str = "Он шёл домой.") -> SemanticUnit:
    return SemanticUnit(
        unit_id="u-1",
        document_id="target-doc",
        block_id="b-1",
        ordinal=0,
        text=text,
        normalized_text=text.casefold(),
        source_start=0,
        source_end=len(text),
        kind="paragraph",
        inline_spans=(SemanticInlineSpan("i-1", 0, len(text), 0, len(text)),),
    )


def test_an_unchanged_answer_is_reused(tmp_path: Path):
    """Re-checking an untouched chapter must not repeat the request."""
    cache = LanguageRuleCache(tmp_path)
    key = _key()

    cache.put(key, (_match(),))

    stored = cache.get(key)
    assert stored is not None
    assert stored[0].rule_id == "MORFOLOGIK_RULE_RU_RU"
    assert stored[0].replacements == ("Он",)


@pytest.mark.parametrize(
    "difference",
    [
        {"text_fingerprint": fingerprint_text("Другой текст.")},
        {"language": "en"},
        {"endpoint": "https://other.example/v2/check"},
        {"server_version": "6.7"},
        {"disabled_rule_ids": ()},
        {"preprocessing_version": "semantic-units-v2"},
    ],
)
def test_anything_that_changes_the_answer_is_a_miss(tmp_path: Path, difference):
    """Reusing an answer produced under other rules would report the wrong thing."""
    cache = LanguageRuleCache(tmp_path)
    cache.put(_key(), (_match(),))

    assert cache.get(_key(**difference)) is None


def test_an_expired_entry_is_a_miss(tmp_path: Path):
    """A rule server changes over time; an old answer must not outlive its TTL."""
    cache = LanguageRuleCache(tmp_path, ttl_seconds=0.0001)
    key = _key()
    cache.put(key, (_match(),))
    path = next(tmp_path.rglob("*.json"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["stored_at"] = 0
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert cache.get(key) is None


def test_a_damaged_entry_is_a_miss_not_a_crash(tmp_path: Path):
    """A truncated cache file must cost one request, never the session."""
    cache = LanguageRuleCache(tmp_path)
    key = _key()
    cache.put(key, (_match(),))
    path = next(tmp_path.rglob("*.json"))
    path.write_text("{ broken", encoding="utf-8")

    assert cache.get(key) is None


def test_the_cache_never_stores_the_endpoint_secrets_or_the_chapter(tmp_path: Path):
    """A disposable cache must be safe to keep and safe to delete."""
    cache = LanguageRuleCache(tmp_path)
    cache.put(_key(), (_match(),))
    stored = json.loads(next(tmp_path.rglob("*.json")).read_text(encoding="utf-8"))

    assert "endpoint" not in json.dumps(stored)
    assert "127.0.0.1" not in json.dumps(stored)


class _CountingProvider:
    endpoint = "http://127.0.0.1:8081/v2/check"
    server_version = "6.6"

    def __init__(self) -> None:
        self.calls = 0

    async def check(self, request):
        self.calls += 1
        return (_match(),)


def test_the_service_reuses_the_cache_across_runs(tmp_path: Path):
    """A second pass over an unchanged chapter must cost no request at all."""
    provider = _CountingProvider()
    service = LanguageRuleService(
        provider=provider,
        cache=LanguageRuleCache(tmp_path),
        preprocessing_version="semantic-units-v1",
    )
    capabilities = QaCapabilitySettings(language_tool_enabled=True)

    first = asyncio.run(service.collect((_unit(),), capabilities))
    second = asyncio.run(service.collect((_unit(),), capabilities))

    assert provider.calls == 1
    assert first.issues == second.issues
    assert second.status == "completed"


def test_changed_text_bypasses_the_cache(tmp_path: Path):
    """An edited chapter must be checked again, not answered from history."""
    provider = _CountingProvider()
    service = LanguageRuleService(provider=provider, cache=LanguageRuleCache(tmp_path))
    capabilities = QaCapabilitySettings(language_tool_enabled=True)

    asyncio.run(service.collect((_unit(),), capabilities))
    asyncio.run(service.collect((_unit("Он вернулся домой."),), capabilities))

    assert provider.calls == 2
