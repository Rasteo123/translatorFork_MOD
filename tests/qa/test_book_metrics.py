import math

import pandas as pd
import pytest

from gemini_translator.qa.book_metrics import (
    BookMetricsAnalyzer,
    RelativeRisk,
)
from gemini_translator.qa.models import ChapterMetrics, RiskLevel


def metric(
    chapter_id: str,
    source_language: str,
    target_language: str,
    source_chars: int,
    translated_chars: int,
    **kwargs: object,
) -> ChapterMetrics:
    return ChapterMetrics(
        chapter_id=chapter_id,
        source_language=source_language,
        target_language=target_language,
        source_chars=source_chars,
        translated_chars=translated_chars,
        **kwargs,
    )


def test_chinese_ratio_2_8_is_normal_and_languages_are_not_mixed():
    """Mixing the en->ru chapter into the CJK sample would corrupt its baseline."""
    rows = [
        metric(f"zh-{index}", "zh-CN", "ru-RU", 1000, value)
        for index, value in enumerate((2800, 2900, 3000, 2850, 2950))
    ]
    rows.append(metric("en-1", "en", "ru", 1000, 900))

    frame = BookMetricsAnalyzer().analyze(rows)
    result = BookMetricsAnalyzer().classify_ratio_risk(frame, "zh-0")

    assert set(frame["source_language"]) == {"zh", "en"}
    assert set(frame["target_language"]) == {"ru"}
    assert result.absolute_profile == "cjk_to_ru"
    assert result.within_absolute_profile is True
    assert result.baseline.sample_size == 5
    assert result.auto_fix_allowed is False


def test_iqr_is_used_when_mad_is_zero():
    """Dropping the IQR fallback makes this non-uniform book look zero-scale."""
    ratios = (2.8, 2.8, 2.8, 2.8, 3.0, 3.2)
    frame = BookMetricsAnalyzer().analyze(
        metric(str(index), "zh", "ru", 1000, int(ratio * 1000))
        for index, ratio in enumerate(ratios)
    )

    baseline = BookMetricsAnalyzer().ratio_baseline(frame, "5")

    assert baseline.scale_method == "iqr"
    assert baseline.scale is not None
    assert baseline.scale > 0


def test_four_eligible_chapters_leave_relative_risk_unavailable():
    """Lowering the five-chapter minimum would fabricate a book baseline."""
    frame = BookMetricsAnalyzer().analyze(
        metric(str(index), "en", "ru", 1000, 1000 + index)
        for index in range(4)
    )

    result = BookMetricsAnalyzer().classify_ratio_risk(frame, "0")

    assert result.baseline.sample_size == 4
    assert result.baseline.median is None
    assert result.baseline.scale is None
    assert result.relative_risk is RelativeRisk.UNAVAILABLE
    assert result.robust_z is None


def test_short_service_and_failed_chapters_do_not_form_baseline():
    """Including excluded rows here changes the eligible sample from five to eight."""
    rows = [metric(f"good-{index}", "en", "ru", 500, 500) for index in range(5)]
    rows.extend(
        (
            metric("short", "en", "ru", 499, 499),
            metric("service", "en", "ru", 1000, 1000, content_kind="toc"),
            metric(
                "failed",
                "en",
                "ru",
                1000,
                1000,
                risk_level=RiskLevel.FAILED,
            ),
        )
    )
    frame = BookMetricsAnalyzer().analyze(rows)

    baseline = BookMetricsAnalyzer().ratio_baseline(frame, "good-0")

    assert baseline.sample_size == 5


@pytest.mark.parametrize(
    ("threshold", "expected_risk"),
    [
        (2.5, RelativeRisk.MEDIUM),
        (3.5, RelativeRisk.HIGH),
    ],
)
def test_relative_risk_thresholds_are_inclusive(threshold, expected_risk):
    """Using > instead of >= at a documented threshold hides boundary anomalies."""
    rows = [
        metric("a", "en", "ru", 1000, 8000),
        metric("b", "en", "ru", 1000, 9000),
        metric("c", "en", "ru", 1000, 9000),
        metric("d", "en", "ru", 1000, 10000),
        metric("e", "en", "ru", 1000, 11000),
        metric("target", "en", "ru", 1000, 12000),
    ]
    frame = BookMetricsAnalyzer().analyze(rows)
    frame.loc[frame["chapter_id"].eq("target"), "length_ratio"] = (
        9.5 + threshold * 1.4826
    )

    result = BookMetricsAnalyzer().classify_ratio_risk(frame, "target")

    assert result.robust_z == pytest.approx(threshold)
    assert result.relative_risk is expected_risk


def test_absolute_profile_violation_is_independent_from_relative_risk():
    """Removing the absolute branch would stop a 2.7 CJK ratio being escalated."""
    frame = BookMetricsAnalyzer().analyze(
        [
            metric("target", "zh", "ru", 1000, 2700),
            *[
                metric(f"normal-{index}", "zh", "ru", 1000, value)
                for index, value in enumerate((2800, 2800, 2800, 2900, 2900))
            ],
        ]
    )

    result = BookMetricsAnalyzer().classify_ratio_risk(frame, "target")

    assert result.within_absolute_profile is False
    assert result.requires_deep_check is True
    assert result.auto_fix_allowed is False


def test_zero_scale_preserves_signed_infinite_relative_deviation():
    """Coercing zero-scale deviations to zero would hide a target ratio below its median."""
    frame = BookMetricsAnalyzer().analyze(
        [
            *[metric(f"normal-{index}", "en", "ru", 1000, 1000) for index in range(4)],
            metric("target", "en", "ru", 1000, 0),
        ]
    )

    result = BookMetricsAnalyzer().classify_ratio_risk(frame, "target")

    assert result.baseline.scale == 0.0
    assert result.robust_z == -math.inf
    assert result.relative_risk is RelativeRisk.HIGH


def test_unsupported_profile_and_nonfinite_target_do_not_crash_analysis():
    """A missing profile or NaN target must remain explicit instead of aborting a book."""
    analyzer = BookMetricsAnalyzer()
    frame = analyzer.analyze(
        metric(str(index), "en", "de", 1000, 1000) for index in range(5)
    )
    frame.loc[frame["chapter_id"].eq("0"), "length_ratio"] = math.nan

    result = analyzer.classify_ratio_risk(frame, "0")

    assert result.absolute_profile == "unsupported"
    assert result.within_absolute_profile is None
    assert result.robust_z is None
    assert result.relative_risk is RelativeRisk.UNAVAILABLE


def test_empty_duplicate_and_unknown_chapter_ids_are_handled_explicitly():
    """Silently choosing one duplicate or unknown chapter produces an arbitrary result."""
    analyzer = BookMetricsAnalyzer()
    empty = analyzer.analyze(())

    assert isinstance(empty, pd.DataFrame)
    assert empty.empty
    assert list(empty.columns) == list(ChapterMetrics.dataframe_columns())
    with pytest.raises(KeyError, match="unknown chapter_id"):
        analyzer.ratio_baseline(empty, "missing")
    with pytest.raises(ValueError, match="duplicate chapter_id"):
        analyzer.analyze(
            (
                metric("same", "en", "ru", 1000, 1000),
                metric("same", "en", "ru", 1000, 1000),
            )
        )
