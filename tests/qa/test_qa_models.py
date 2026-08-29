from dataclasses import FrozenInstanceError
import math

import pytest

from gemini_translator.qa.capabilities import QaCapabilityKey
from gemini_translator.qa.models import (
    Action,
    CandidateKind,
    ChapterMetrics,
    Decision,
    QaModelValidationError,
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


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("quality_score", math.nan),
        ("quality_score", math.inf),
        ("duration_seconds", -math.inf),
        ("capability_durations", {QaCapabilityKey.RAZDEL: math.nan}),
    ],
)
def test_chapter_metrics_rejects_non_finite_numeric_values(field, value):
    """Removing finite-number validation would let invalid JSON values persist."""
    with pytest.raises(QaModelValidationError, match="finite"):
        ChapterMetrics(
            chapter_id="chapter-1",
            source_language="zh",
            target_language="ru",
            source_chars=1,
            translated_chars=1,
            **{field: value},
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("chapter_id", 1),
        ("source_language", ["zh"]),
        ("target_language", {"code": "ru"}),
        ("quality_score", math.nan),
        ("duration_seconds", math.inf),
        ("length_ratio", -math.inf),
    ],
)
def test_chapter_metrics_from_dict_rejects_wrong_or_non_finite_values(field, value):
    """Dropping persisted type checks would silently accept malformed metrics."""
    payload = ChapterMetrics(
        chapter_id="chapter-1",
        source_language="zh",
        target_language="ru",
    ).to_dict()
    payload[field] = value

    with pytest.raises(QaModelValidationError):
        ChapterMetrics.from_dict(payload)


def test_risk_level_includes_failed_for_excluded_book_baselines():
    """Removing the failed state breaks Task 5's baseline eligibility contract."""
    assert "failed" in {level.value for level in RiskLevel}


def test_chapter_metrics_from_dict_rejects_contradictory_persisted_ratio():
    """Ignoring persisted length_ratio would silently hide corrupted v1 metrics."""
    payload = ChapterMetrics(
        chapter_id="chapter-1",
        source_language="zh",
        target_language="ru",
        source_chars=100,
        translated_chars=200,
    ).to_dict()
    payload["length_ratio"] = 999.0

    with pytest.raises(QaModelValidationError, match="length_ratio"):
        ChapterMetrics.from_dict(payload)


@pytest.mark.parametrize(
    ("source_chars", "translated_chars", "expected_ratio"),
    [(100, 200, 2.0), (0, 25, 0.0)],
)
def test_chapter_metrics_from_dict_accepts_derived_ratio_contract(
    source_chars, translated_chars, expected_ratio
):
    """Valid persisted ratios, including the zero-source representation, round-trip."""
    metrics = ChapterMetrics(
        chapter_id="chapter-1",
        source_language="zh",
        target_language="ru",
        source_chars=source_chars,
        translated_chars=translated_chars,
    )

    restored = ChapterMetrics.from_dict(metrics.to_dict())

    assert restored.length_ratio == expected_ratio
