"""cluster-12: truncate_auto_trace_text скопирована из core в UI-диалог.

Канон: gemini_translator.core.auto_workflow_helpers.truncate_auto_trace_text.
setup.py::InitialSetupDialog._truncate_auto_trace_text уже делегирует к каноной
версии. validation.py::TranslationValidatorPage._truncate_auto_trace_text должен
делегировать так же (был независимой копией тела функции).
"""
from unittest import mock

from gemini_translator.core import auto_workflow_helpers
from gemini_translator.core.auto_workflow_helpers import truncate_auto_trace_text
from gemini_translator.ui.dialogs.setup import InitialSetupDialog
from gemini_translator.ui.dialogs.validation import TranslationValidatorPage


# --- (a) характеризационные тесты канонической реализации -----------------

def test_truncate_auto_trace_text_returns_stripped_text_under_limit():
    assert truncate_auto_trace_text("  hello world  ", limit=4000) == "hello world"


def test_truncate_auto_trace_text_handles_none():
    assert truncate_auto_trace_text(None) == ""


def test_truncate_auto_trace_text_exact_limit_not_truncated():
    text = "a" * 20
    assert truncate_auto_trace_text(text, limit=20) == text


def test_truncate_auto_trace_text_over_limit_truncates_with_marker():
    assert truncate_auto_trace_text("abcdefghijklmnopqrstuvwxyz", limit=20) == (
        "abcd\n...[truncated]..."
    )


def test_truncate_auto_trace_text_rstrips_before_marker():
    # limit=20 -> keep first max(0, limit-16)=4 chars, then rstrip before
    # appending the marker. With trailing spaces inside the kept slice they
    # must be stripped so the marker doesn't follow whitespace.
    text = "ab   " + "x" * 30
    truncated = truncate_auto_trace_text(text, limit=20)
    assert truncated == "ab\n...[truncated]..."


# --- (b) тест-маршрутизация: оба UI-диалога обязаны идти через canonical ---

def test_setup_dialog_routes_through_canonical_truncate():
    with mock.patch.object(
        auto_workflow_helpers, "truncate_auto_trace_text", return_value="ROUTED"
    ) as canonical:
        result = InitialSetupDialog._truncate_auto_trace_text("some text", limit=10)
    canonical.assert_called_once_with("some text", limit=10)
    assert result == "ROUTED"


def test_validation_dialog_routes_through_canonical_truncate():
    with mock.patch.object(
        auto_workflow_helpers, "truncate_auto_trace_text", return_value="ROUTED"
    ) as canonical:
        result = TranslationValidatorPage._truncate_auto_trace_text("some text", limit=10)
    canonical.assert_called_once_with("some text", limit=10)
    assert result == "ROUTED"
