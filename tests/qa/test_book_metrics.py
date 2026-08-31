import math

import pandas as pd
import pytest

from gemini_translator.qa.book_metrics import (
    BookMetricsAnalyzer,
    RelativeRisk,
)
from gemini_translator.qa.journal import QaJournal
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
    assert baseline.scale == pytest.approx((2.95 - 2.8) / 1.349)


def test_raw_journal_frame_normalizes_language_variants_without_mutating_it():
    """Using raw journal languages used to split one CJK baseline into five samples."""
    journal = QaJournal.empty(book_id="book")
    for index, source_language in enumerate(("zh-CN", "zh", "zh-TW", "zh", "zh")):
        journal.upsert_metrics(
            metric(
                f"chapter-{index}",
                source_language,
                "ru-RU" if index == 0 else "ru",
                1000,
                2800 + index * 10,
            )
        )
    raw_frame = journal.metrics_frame()

    result = BookMetricsAnalyzer().classify_ratio_risk(raw_frame, "chapter-0")

    assert raw_frame.loc[raw_frame["chapter_id"].eq("chapter-0"), "source_language"].item() == "zh-CN"
    assert raw_frame.loc[raw_frame["chapter_id"].eq("chapter-0"), "target_language"].item() == "ru-RU"
    assert result.baseline.language_pair == ("zh", "ru")
    assert result.baseline.sample_size == 5


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


@pytest.mark.parametrize(
    ("translated_chars", "expected_robust_z", "expected_risk"),
    [
        (2000, math.inf, RelativeRisk.HIGH),
        (0, -math.inf, RelativeRisk.HIGH),
        (1000, 0.0, RelativeRisk.LOW),
    ],
)
def test_zero_scale_distinguishes_positive_negative_and_no_deviation(
    translated_chars, expected_robust_z, expected_risk
):
    """Replacing signed infinities or zero with one fallback value hides a real branch."""
    frame = BookMetricsAnalyzer().analyze(
        [
            *[metric(f"normal-{index}", "en", "ru", 1000, 1000) for index in range(4)],
            metric("target", "en", "ru", 1000, translated_chars),
        ]
    )

    result = BookMetricsAnalyzer().classify_ratio_risk(frame, "target")

    assert result.baseline.scale == 0.0
    assert result.robust_z == expected_robust_z
    assert result.relative_risk is expected_risk


def test_supported_profile_with_nonfinite_target_has_no_absolute_verdict():
    """Turning an invalid CJK ratio into an absolute violation creates a false alert."""
    analyzer = BookMetricsAnalyzer()
    frame = analyzer.analyze(
        metric(str(index), "zh", "ru", 1000, 2800) for index in range(5)
    )
    frame.loc[frame["chapter_id"].eq("0"), "length_ratio"] = math.nan

    result = analyzer.classify_ratio_risk(frame, "0")

    assert result.absolute_profile == "cjk_to_ru"
    assert result.within_absolute_profile is None
    assert result.robust_z is None
    assert result.relative_risk is RelativeRisk.UNAVAILABLE
    assert result.requires_deep_check is False


@pytest.mark.parametrize(
    ("source_language", "ratio", "profile", "within_profile"),
    [
        ("en", 0.92, "alphabetic_to_ru", True),
        ("en", 1.20, "alphabetic_to_ru", True),
        ("en", 0.919, "alphabetic_to_ru", False),
        ("en", 1.201, "alphabetic_to_ru", False),
        ("zh", 2.80, "cjk_to_ru", True),
        ("zh", 3.30, "cjk_to_ru", True),
        ("zh", 3.80, "cjk_to_ru", True),
        ("zh", 2.799, "cjk_to_ru", False),
        ("zh", 3.801, "cjk_to_ru", False),
    ],
)
def test_absolute_profiles_include_documented_endpoints_only(
    source_language, ratio, profile, within_profile
):
    """Moving an inclusive endpoint or accepting its neighbour changes the profile contract."""
    translated_chars = int(ratio * 10000)
    frame = BookMetricsAnalyzer().analyze(
        metric(str(index), source_language, "ru", 10000, translated_chars)
        for index in range(5)
    )

    result = BookMetricsAnalyzer().classify_ratio_risk(frame, "0")

    assert result.absolute_profile == profile
    assert result.within_absolute_profile is within_profile
    assert result.requires_deep_check is not within_profile


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
