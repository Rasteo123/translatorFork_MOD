"""pcluster-14: the "nonempty string" / "bounded int" families in qa/.

This cluster is heterogeneous (see the cluster report). Two sub-families are
genuinely the same paint-by-numbers algorithm copied under different names
with different typed exceptions wrapped around it, and are safe to unify:

  (A) strip-and-return nonempty-string validator:
      qa.coverage_service._nonempty_string, qa.embeddings.base._nonempty_string,
      qa.repair_store._identity, and the three qa.embeddings.cache copies that
      duplicate qa.embeddings.base's helpers instead of importing them
      (_nonempty, _positive_int, _normalized_language).
  (B) bounded-int clamp with a swapped parameter order between copies:
      qa.settings._bounded_int(value, default, minimum, maximum) vs
      qa.russian_nlp.slovnet_provider._bounded(value, minimum, maximum, default).

Everything else in the cluster (qa.llm.schemas._nonempty_string /
qa.estimators.base._nonempty which do NOT strip on return, qa.models's
void-validator, the _base_language family, and _safe_int which was already
unified into utils.helpers.safe_int by an earlier dedup phase) is
deliberately left alone and is NOT covered by this file.

This suite:
1. Characterizes the shared primitives in qa._common directly, including the
   edge cases that distinguish the (pre-refactor) copies.
2. Characterizes each caller's observable behavior (message text, exception
   type) so unification does not change what callers see.
3. Proves every former copy now routes through qa._common (a routing test
   that must fail before the refactor and pass after).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from gemini_translator.qa import _common


# ---------------------------------------------------------------------------
# 1. Shared primitives
# ---------------------------------------------------------------------------


class TestValidateNonemptyString:
    def test_strips_and_returns(self):
        assert _common.validate_nonempty_string("  hi  ", "field") == "hi"

    def test_returns_value_unchanged_when_no_surrounding_whitespace(self):
        assert _common.validate_nonempty_string("hi", "field") == "hi"

    def test_rejects_empty_string(self):
        with pytest.raises(ValueError, match="field must be a nonempty string"):
            _common.validate_nonempty_string("", "field")

    def test_rejects_whitespace_only_string(self):
        with pytest.raises(ValueError, match="field must be a nonempty string"):
            _common.validate_nonempty_string("   ", "field")

    def test_rejects_non_string(self):
        with pytest.raises(ValueError, match="field must be a nonempty string"):
            _common.validate_nonempty_string(123, "field")

    def test_rejects_none(self):
        with pytest.raises(ValueError, match="field must be a nonempty string"):
            _common.validate_nonempty_string(None, "field")

    def test_message_includes_field_name(self):
        with pytest.raises(ValueError, match=r"^chapter_id must be a nonempty string$"):
            _common.validate_nonempty_string("", "chapter_id")


class TestBoundedInt:
    def test_clamps_to_minimum(self):
        assert _common.bounded_int(-5, default=2, minimum=1, maximum=32) == 1

    def test_clamps_to_maximum(self):
        assert _common.bounded_int(999, default=2, minimum=1, maximum=32) == 32

    def test_passes_through_in_range_value(self):
        assert _common.bounded_int(16, default=2, minimum=1, maximum=32) == 16

    def test_falls_back_to_default_for_non_int(self):
        assert _common.bounded_int("16", default=2, minimum=1, maximum=32) == 2
        assert _common.bounded_int(None, default=2, minimum=1, maximum=32) == 2
        assert _common.bounded_int(1.5, default=2, minimum=1, maximum=32) == 2

    def test_falls_back_to_default_for_bool(self):
        # bool is an int subclass; both families explicitly exclude it.
        assert _common.bounded_int(True, default=2, minimum=1, maximum=32) == 2
        assert _common.bounded_int(False, default=2, minimum=1, maximum=32) == 2

    def test_is_keyword_only_for_default_minimum_maximum(self):
        with pytest.raises(TypeError):
            _common.bounded_int(16, 2, 1, 32)  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 2 & 3. Callers: characterization + routing
# ---------------------------------------------------------------------------


class TestCoverageServiceNonemptyString:
    def test_strips_and_returns(self):
        from gemini_translator.qa import coverage_service

        assert coverage_service._nonempty_string("  x  ", "field") == "x"

    def test_raises_typed_exception_on_empty(self):
        from gemini_translator.qa import coverage_service

        with pytest.raises(coverage_service.CoverageValidationError, match="field must be a nonempty string"):
            coverage_service._nonempty_string("", "field")

    def test_routes_through_shared_primitive(self, monkeypatch):
        from gemini_translator.qa import coverage_service

        calls = []

        def fake(value, field_name):
            calls.append((value, field_name))
            return "stubbed"

        monkeypatch.setattr(coverage_service, "_validate_nonempty_string", fake)
        assert coverage_service._nonempty_string("x", "field") == "stubbed"
        assert calls == [("x", "field")]

    def test_shared_primitive_valueerror_becomes_typed_exception(self, monkeypatch):
        from gemini_translator.qa import coverage_service

        def raiser(value, field_name):
            raise ValueError(f"{field_name} must be a nonempty string")

        monkeypatch.setattr(coverage_service, "_validate_nonempty_string", raiser)
        with pytest.raises(coverage_service.CoverageValidationError):
            coverage_service._nonempty_string("", "field")


class TestRepairStoreIdentity:
    def test_strips_and_returns(self):
        from gemini_translator.qa import repair_store

        assert repair_store._identity("  chap-1  ", "chapter_id") == "chap-1"

    def test_raises_typed_exception_on_empty(self):
        from gemini_translator.qa import repair_store

        with pytest.raises(repair_store.RepairStoreError, match="chapter_id must be a nonempty string"):
            repair_store._identity("", "chapter_id")

    def test_routes_through_shared_primitive(self, monkeypatch):
        from gemini_translator.qa import repair_store

        calls = []

        def fake(value, field_name):
            calls.append((value, field_name))
            return "stubbed"

        monkeypatch.setattr(repair_store, "_validate_nonempty_string", fake)
        assert repair_store._identity("x", "chapter_id") == "stubbed"
        assert calls == [("x", "chapter_id")]


class TestEmbeddingsNonemptyFamily:
    def test_base_strips_and_returns(self):
        from gemini_translator.qa.embeddings import base

        assert base._nonempty_string("  x  ", "field") == "x"

    def test_base_raises_typed_exception_on_empty(self):
        from gemini_translator.qa.embeddings import base

        with pytest.raises(base.EmbeddingContractError, match="field must be a nonempty string"):
            base._nonempty_string("", "field")

    def test_base_positive_int_rejects_bool_and_nonpositive(self):
        from gemini_translator.qa.embeddings import base

        assert base._positive_int(5, "dimensions") == 5
        with pytest.raises(base.EmbeddingContractError):
            base._positive_int(True, "dimensions")
        with pytest.raises(base.EmbeddingContractError):
            base._positive_int(0, "dimensions")

    def test_cache_nonempty_matches_base_behavior(self):
        from gemini_translator.qa.embeddings import cache

        assert cache._nonempty("  x  ", "field") == "x"
        with pytest.raises(cache.EmbeddingContractError, match="field must be a nonempty string"):
            cache._nonempty("", "field")

    def test_cache_positive_int_matches_base_behavior(self):
        from gemini_translator.qa.embeddings import cache

        assert cache._positive_int(5, "dimensions") == 5
        with pytest.raises(cache.EmbeddingContractError):
            cache._positive_int(0, "dimensions")

    def test_cache_normalized_language_matches_base_base_language(self):
        from gemini_translator.qa.embeddings import base, cache

        assert cache._normalized_language("EN-us") == base._base_language("EN-us")
        assert cache._normalized_language("ru_RU") == "ru"

    def test_base_and_cache_nonempty_string_route_through_shared_primitive(self, monkeypatch):
        """cache.py must not keep a private copy: patching base's internal hook
        has to affect calls made through cache's imported name too, since they
        are meant to be the exact same function object."""
        from gemini_translator.qa.embeddings import base, cache

        calls = []

        def fake(value, field_name):
            calls.append((value, field_name))
            return "stubbed"

        monkeypatch.setattr(base, "_validate_nonempty_string", fake)
        assert base._nonempty_string("a", "f1") == "stubbed"
        assert cache._nonempty("b", "f2") == "stubbed"
        assert calls == [("a", "f1"), ("b", "f2")]


class TestSettingsBoundedInt:
    def test_clamps_like_shared_primitive(self):
        from gemini_translator.qa import settings

        assert settings._bounded_int(999, default=2, minimum=1, maximum=32) == 32
        assert settings._bounded_int(-1, default=2, minimum=1, maximum=32) == 1
        assert settings._bounded_int("nope", default=2, minimum=1, maximum=32) == 2

    def test_is_the_shared_primitive_object(self):
        """A real routing check: settings._bounded_int must be an import of
        qa._common.bounded_int, not a second copy of the same body — this is
        the assertion that fails before the refactor (settings defines its
        own function object) and passes after (it imports the shared one)."""
        from gemini_translator.qa import _common, settings

        assert settings._bounded_int is _common.bounded_int

    def test_dataclass_call_sites_use_keyword_arguments(self):
        """The historical bug in this cluster was copy-pasting positional
        (default, minimum, maximum) into a callee that expected
        (minimum, maximum, default) or vice-versa. Once the shared primitive
        is keyword-only, a stray positional call site would raise TypeError
        at settings-construction time, not silently swap bounds."""
        from gemini_translator.qa.settings import QaSettings

        settings_obj = QaSettings(slovnet_cpu_threads=999, batch_concurrency=999)
        assert settings_obj.slovnet_cpu_threads == 32
        assert settings_obj.batch_concurrency == 4


class TestSlovnetProviderBounded:
    def test_clamps_like_shared_primitive(self):
        from gemini_translator.qa.russian_nlp import slovnet_provider

        assert slovnet_provider._bounded(999, minimum=1, maximum=32, default=2) == 32
        assert slovnet_provider._bounded(-1, minimum=1, maximum=32, default=2) == 1
        assert slovnet_provider._bounded("nope", minimum=1, maximum=32, default=2) == 2

    def test_is_the_shared_primitive_object(self):
        """Same real routing check as settings, on the copy whose parameter
        order (minimum, maximum, default) was swapped relative to
        qa.settings._bounded_int(default, minimum, maximum) — the exact
        copy-paste trap this cluster's evidence calls out."""
        from gemini_translator.qa import _common
        from gemini_translator.qa.russian_nlp import slovnet_provider

        assert slovnet_provider._bounded is _common.bounded_int

    def test_constructor_clamps_cpu_threads_and_batch_size(self):
        from gemini_translator.qa.russian_nlp.slovnet_provider import (
            MAX_BATCH_SIZE,
            MAX_CPU_THREADS,
            SlovnetProvider,
        )

        provider = SlovnetProvider(runtime=object(), cpu_threads=999, batch_size=999)
        assert provider.cpu_threads == MAX_CPU_THREADS
        assert provider.batch_size == MAX_BATCH_SIZE
