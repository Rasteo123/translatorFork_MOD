from dataclasses import FrozenInstanceError

import pytest

from gemini_translator.qa.capabilities import QaCapabilityKey
from gemini_translator.qa.models import (
    Action,
    CandidateKind,
    ChapterMetrics,
    Decision,
    RiskLevel,
)


def test_chapter_metrics_round_trips_plain_json_values_and_is_immutable():
    """Removing enum-to-string conversion or frozen=True breaks this contract."""
    metrics = ChapterMetrics(
        chapter_id="chapter-1",
        source_language="zh",
        target_language="ru",
        source_chars=1000,
        translated_chars=2800,
        capability_durations={QaCapabilityKey.RAZDEL: 0.25},
        risk_level=RiskLevel.LOW,
        applied_actions=(Action.REPORT_ONLY,),
    )

    serialized = metrics.to_dict()

    assert serialized["capability_durations"] == {"razdel": 0.25}
    assert serialized["risk_level"] == "low"
    assert serialized["applied_actions"] == ["report_only"]
    assert ChapterMetrics.from_dict(serialized) == metrics
    with pytest.raises(FrozenInstanceError):
        metrics.source_chars = 1


def test_chapter_metrics_uses_safe_ratio_for_empty_source_text():
    """Changing the zero-source guard to division would make this fail."""
    metrics = ChapterMetrics(
        chapter_id="empty",
        source_language="zh",
        target_language="ru",
        source_chars=0,
        translated_chars=25,
    )

    assert metrics.length_ratio == 0.0


def test_chapter_metrics_rejects_negative_capability_duration():
    """Dropping duration validation would make corrupt timing data persist."""
    with pytest.raises(ValueError, match="non-negative"):
        ChapterMetrics(
            chapter_id="chapter-1",
            source_language="zh",
            target_language="ru",
            source_chars=1,
            translated_chars=1,
            capability_durations={QaCapabilityKey.LANGUAGE_TOOL: -0.1},
        )


def test_model_enums_keep_the_persisted_values_explicit():
    """Replacing the typed string enums with arbitrary strings loses schema choices."""
    assert RiskLevel.HIGH.value == "high"
    assert CandidateKind.POSSIBLE_GAP.value == "possible_gap"
    assert Decision.WARNING.value == "warning"
    assert Action.REPORT_ONLY.value == "report_only"

