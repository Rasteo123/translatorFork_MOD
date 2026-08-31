"""Which keys embeddings may use, and whose quota they spend."""

from __future__ import annotations

import asyncio

import pytest

from gemini_translator.qa.assembly import (
    SettingsEmbeddingKeyHealth,
    embedding_key_namespace,
    embedding_key_pool,
    embedding_keys_for_session,
    embedding_model_for,
    green_embedding_keys,
)
from gemini_translator.qa.settings import QaSettings


class _SettingsManagerStub:
    """Just enough of the real settings manager to answer about key limits."""

    def __init__(self, keys, blocked=()) -> None:
        self.keys = list(keys)
        self.blocked = {(key, model) for key, model in blocked}
        self.marked: list[tuple[str, str]] = []

    def load_key_statuses(self):
        return [dict(item) for item in self.keys]

    def get_key_info(self, key):
        return next((dict(item) for item in self.keys if item["key"] == key), None)

    def is_key_limit_active(self, key_info, model_id):
        return (key_info.get("key"), model_id) in self.blocked

    def mark_key_as_exhausted(self, key, model_id):
        self.marked.append((key, model_id))
        self.blocked.add((key, model_id))
        return True


def _manager(**overrides):
    return _SettingsManagerStub(
        [
            {"key": "AQ.one", "provider": "gemini"},
            {"key": "AQ.two", "provider": "gemini"},
            {"key": "sk-three", "provider": "openrouter"},
        ],
        **overrides,
    )


def test_a_chosen_provider_contributes_every_key_that_is_still_green():
    """The point of naming a provider is not to depend on one key's mood."""
    manager = _manager(blocked=[("AQ.two", "gemini-embedding-001")])
    settings = QaSettings(embedding_key_provider="gemini")

    pool = embedding_key_pool(manager, settings, {})

    assert pool == {"google": ("AQ.one",)}


def test_a_key_red_for_translation_is_still_green_for_embeddings():
    """Separate limits are the whole request: one quota must not spend the other."""
    manager = _manager(blocked=[("AQ.one", "gemini-3-flash-preview")])
    settings = QaSettings(embedding_key_provider="gemini")

    pool = embedding_key_pool(manager, settings, {})

    assert pool == {"google": ("AQ.one", "AQ.two")}


def test_a_provider_with_no_green_keys_reports_nothing_rather_than_borrowing():
    """Silently falling back to the session key is how this broke in the first place."""
    manager = _manager(
        blocked=[
            ("AQ.one", "gemini-embedding-001"),
            ("AQ.two", "gemini-embedding-001"),
        ]
    )
    settings = QaSettings(embedding_key_provider="gemini")

    pool = embedding_key_pool(manager, settings, {"google": "session-key"})

    assert pool == {}


def test_an_explicit_key_still_wins_over_a_provider_pool():
    """A key typed by hand is the most specific thing the user can say."""
    manager = _manager()
    settings = QaSettings(
        embedding_api_key="AQ.explicit", embedding_key_provider="gemini"
    )

    pool = embedding_key_pool(manager, settings, {"google": "session-key"})

    assert pool == {"google": "session-key"}


def test_without_a_chosen_provider_the_session_key_is_used_as_before():
    """Existing projects must keep working exactly as they did."""
    pool = embedding_key_pool(_manager(), QaSettings(), {"google": "session-key"})

    assert pool == {"google": "session-key"}


def test_a_provider_without_an_embedding_backend_yields_no_keys():
    """A provider that cannot embed anything must not look configured."""
    settings = QaSettings(embedding_key_provider="xai")

    assert embedding_key_namespace("xai") == ""
    assert embedding_key_pool(_manager(), settings, {}) == {}


def test_the_session_mapping_offers_every_key_a_backend_accepts():
    """This is the mapping that used to hand embeddings one key nobody could use."""
    assert embedding_keys_for_session("gemini", "AQ.one") == {"google": ("AQ.one",)}
    assert embedding_keys_for_session("gemini", ["AQ.one", "AQ.two"]) == {
        "google": ("AQ.one", "AQ.two")
    }
    assert embedding_keys_for_session("nvidia", ["nv-key"]) == {}


def test_green_keys_survive_an_unreadable_settings_manager():
    """QA degrades to limited mode; it never takes the session down with it."""

    class _Broken:
        def load_key_statuses(self):
            raise OSError("settings are locked")

    assert green_embedding_keys(_Broken(), "gemini", "m") == ()
    assert green_embedding_keys(None, "gemini", "m") == ()


# --- who goes red, and for what --------------------------------------------


def test_a_used_up_key_goes_red_for_the_embedding_model_only():
    """Marking the translation model would stop the translation this check serves."""
    manager = _manager()
    health = SettingsEmbeddingKeyHealth(manager, "gemini-embedding-001")

    health.mark_exhausted("AQ.one", "quota")

    assert manager.marked == [("AQ.one", "gemini-embedding-001")]
    assert health.is_active("AQ.one") is False
    assert health.is_active("AQ.two") is True


def test_key_health_answers_yes_when_it_cannot_answer_at_all():
    """An unreadable status is not evidence that a key is dead."""

    class _Broken:
        def get_key_info(self, key):
            raise OSError("settings are locked")

        def is_key_limit_active(self, key_info, model_id):
            raise AssertionError("must not be reached")

        def mark_key_as_exhausted(self, key, model_id):
            raise OSError("settings are locked")

    health = SettingsEmbeddingKeyHealth(_Broken(), "gemini-embedding-001")

    assert health.is_active("AQ.one") is True
    health.mark_exhausted("AQ.one", "quota")  # must not raise


def test_the_model_a_key_is_judged_against_follows_the_chosen_backend():
    """Judging a Gemini key against an OpenAI model id would never match anything."""
    assert embedding_model_for(QaSettings(), "google").startswith("gemini")
    assert embedding_model_for(QaSettings(), "openai") != embedding_model_for(
        QaSettings(), "google"
    )
    assert (
        embedding_model_for(QaSettings(embedding_model="custom-model"), "google")
        == "custom-model"
    )


# --- the provider stops using a key it knows is out ------------------------


def _provider(keys, health, responses):
    from gemini_translator.qa.embeddings.gemini import GeminiEmbeddingProvider

    return GeminiEmbeddingProvider(
        keys,
        _session_factory(responses),
        timeout_seconds=5.0,
        retry_sleep=_no_sleep,
        key_health=health,
    )


async def _no_sleep(_seconds):
    return None


def _session_factory(responses):
    used: list[str] = []

    class _Response:
        def __init__(self, item) -> None:
            self._item = item
            self.status = item["status"]

        async def json(self):
            return self._item.get("json", {})

        async def text(self):
            return self._item.get("text", "")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    class _Session:
        def post(self, url, headers=None, json=None, timeout=None):
            used.append((headers or {}).get("x-goog-api-key", ""))
            return _Response(responses[min(len(used) - 1, len(responses) - 1)])

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    factory = lambda: _Session()  # noqa: E731 - one-line stub
    factory.used = used  # type: ignore[attr-defined]
    return factory


def _request():
    from gemini_translator.qa.embeddings.base import EmbeddingRequest

    return EmbeddingRequest(
        texts=("текст",),
        language="ru",
        model="gemini-embedding-001",
        dimensions=None,
        task_type="semantic_similarity",
    )


class _Health:
    def __init__(self, blocked=()) -> None:
        self.blocked = set(blocked)
        self.marked: list[tuple[str, str]] = []

    def is_active(self, api_key):
        return api_key not in self.blocked

    def mark_exhausted(self, api_key, reason=""):
        self.marked.append((api_key, reason))
        self.blocked.add(api_key)


def test_a_key_out_of_quota_is_reported_and_not_used_again():
    """Without this the pool keeps hammering a key the service already refused."""
    from gemini_translator.qa.embeddings.factory import EmbeddingHttpError

    health = _Health()
    provider = _provider(
        ("AQ.one", "AQ.two"),
        health,
        [{"status": 429, "text": "RESOURCE_EXHAUSTED: quota exceeded"}],
    )

    with pytest.raises(EmbeddingHttpError):
        asyncio.run(provider.embed(_request()))

    assert health.marked
    assert health.marked[0][0] == "AQ.one"


def test_a_transient_refusal_rotates_without_condemning_the_key():
    """A busy minute is not an exhausted day; this is the bug this rule exists for."""
    from gemini_translator.qa.embeddings.factory import EmbeddingHttpError

    health = _Health()
    provider = _provider(
        ("AQ.one", "AQ.two"),
        health,
        [{"status": 429, "text": "please slow down"}],
    )

    with pytest.raises(EmbeddingHttpError):
        asyncio.run(provider.embed(_request()))

    assert health.marked == []


def test_a_pool_with_every_key_red_stops_instead_of_asking():
    """Semantic checking then reports itself unavailable, and QA carries on."""
    from gemini_translator.qa.embeddings.factory import EmbeddingUnavailableError

    health = _Health(blocked=("AQ.one", "AQ.two"))
    provider = _provider(("AQ.one", "AQ.two"), health, [{"status": 200}])

    with pytest.raises(EmbeddingUnavailableError):
        asyncio.run(provider.embed(_request()))
