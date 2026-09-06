"""Characterization + routing tests for cluster-09 dedup.

``_positive_finite_timeout`` was defined identically in three modules of
``gemini_translator.qa.embeddings`` (openai_compatible.py, gemini.py,
factory.py). This test module locks down its behavior and then proves every
former call site routes through the single canonical implementation in
``gemini_translator.qa.embeddings.base``.
"""

from __future__ import annotations

import math
from unittest import mock

import pytest

from gemini_translator.qa.embeddings.base import (
    EmbeddingContractError,
    _positive_finite_timeout,
)


# ---------------------------------------------------------------------------
# (a) Characterization tests on the canonical implementation.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [1, 1.0, 30, 30.5, 0.001, 10_000])
def test_accepts_positive_finite_numbers(value):
    result = _positive_finite_timeout(value)
    assert result == float(value)
    assert isinstance(result, float)


@pytest.mark.parametrize("value", [0, 0.0, -1, -0.5])
def test_rejects_non_positive_numbers(value):
    with pytest.raises(EmbeddingContractError):
        _positive_finite_timeout(value)


@pytest.mark.parametrize("value", [math.inf, -math.inf, math.nan])
def test_rejects_non_finite_numbers(value):
    with pytest.raises(EmbeddingContractError):
        _positive_finite_timeout(value)


@pytest.mark.parametrize("value", [True, False])
def test_rejects_bool_even_though_bool_is_int_subclass(value):
    with pytest.raises(EmbeddingContractError):
        _positive_finite_timeout(value)


@pytest.mark.parametrize("value", ["30", None, [], {}, object()])
def test_rejects_non_numeric_values(value):
    with pytest.raises(EmbeddingContractError):
        _positive_finite_timeout(value)


def test_error_message_mentions_field_name():
    with pytest.raises(EmbeddingContractError, match="timeout_seconds must be a positive finite number"):
        _positive_finite_timeout(-1)


# ---------------------------------------------------------------------------
# (b) Routing tests: every former call site must go through the canonical
# implementation. These FAIL before the refactor (each site used its own
# private copy) and PASS after (each site imports base._positive_finite_timeout).
# ---------------------------------------------------------------------------


def test_factory_embedding_provider_config_routes_through_canonical():
    from gemini_translator.qa.embeddings import factory

    with mock.patch.object(
        factory, "_positive_finite_timeout", wraps=factory._positive_finite_timeout
    ) as spy:
        factory.EmbeddingProviderConfig(kind="gemini", api_key="k", timeout_seconds=5)
    spy.assert_called_once_with(5)


def test_gemini_provider_routes_through_canonical():
    from gemini_translator.qa.embeddings import gemini as gemini_module

    with mock.patch.object(
        gemini_module, "_positive_finite_timeout", wraps=gemini_module._positive_finite_timeout
    ) as spy:
        gemini_module.GeminiEmbeddingProvider("key", session_factory=lambda: None, timeout_seconds=7)
    spy.assert_called_once_with(7)


def test_openai_compatible_provider_routes_through_canonical():
    from gemini_translator.qa.embeddings import openai_compatible

    with mock.patch.object(
        openai_compatible,
        "_positive_finite_timeout",
        wraps=openai_compatible._positive_finite_timeout,
    ) as spy:
        openai_compatible.OpenAICompatibleEmbeddingProvider(
            "http://example.com", "key", session_factory=lambda: None, timeout_seconds=9
        )
    spy.assert_called_once_with(9)


def test_all_three_modules_reference_the_same_canonical_function_object():
    """After dedup, factory/gemini/openai_compatible must all bind the exact
    same function object from base — not merely equal-looking copies."""
    from gemini_translator.qa.embeddings import base, factory, gemini as gemini_module, openai_compatible

    assert factory._positive_finite_timeout is base._positive_finite_timeout
    assert gemini_module._positive_finite_timeout is base._positive_finite_timeout
    assert openai_compatible._positive_finite_timeout is base._positive_finite_timeout
