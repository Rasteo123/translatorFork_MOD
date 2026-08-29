"""In-memory, language-aware chapter length analysis for a translated book."""

from dataclasses import dataclass
from enum import StrEnum
import math
from typing import Iterable

import numpy as np
import pandas as pd

from .models import ChapterMetrics
from .ratio_profiles import get_ratio_profile


MIN_BASELINE_SOURCE_CHARS = 500
MIN_BASELINE_SAMPLE_SIZE = 5
MEDIUM_ROBUST_Z_THRESHOLD = 2.5
HIGH_ROBUST_Z_THRESHOLD = 3.5


class RelativeRisk(StrEnum):
    """Relative book-level length risk, separate from absolute pair profiles."""

    UNAVAILABLE = "unavailable"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True, slots=True)
class BookRatioBaseline:
    """Robust ratio statistics for one normalized source/target language pair."""

    language_pair: tuple[str, str]
    sample_size: int
    median: float | None
    scale: float | None
    scale_method: str | None


@dataclass(frozen=True, slots=True)
class RatioRisk:
    """Independent absolute-profile and relative-book signals for one chapter."""

    baseline: BookRatioBaseline
    absolute_profile: str
    within_absolute_profile: bool | None
    robust_z: float | None
    relative_risk: RelativeRisk
    requires_deep_check: bool
    auto_fix_allowed: bool = False


class BookMetricsAnalyzer:
    """Build DataFrames and compare chapters only with their own language pair."""

    def analyze(self, metrics: Iterable[ChapterMetrics]) -> pd.DataFrame:
        """Return an in-memory metrics frame with normalized base ISO languages."""
        rows = [metric.to_dict() for metric in metrics]
        frame = pd.DataFrame(rows, columns=ChapterMetrics.dataframe_columns())
        if frame.empty:
            return frame

        chapter_ids = frame["chapter_id"].astype("string")
        if chapter_ids.duplicated().any():
            raise ValueError("duplicate chapter_id values are not supported")

        frame["source_language"] = _base_language_series(frame["source_language"])
        frame["target_language"] = _base_language_series(frame["target_language"])
        return frame

    def ratio_baseline(
        self, frame: pd.DataFrame, chapter_id: str
    ) -> BookRatioBaseline:
        """Return the robust eligible baseline for ``chapter_id``'s language pair."""
        chapter = _chapter_row(frame, chapter_id)
        source_language = _base_language(chapter["source_language"])
        target_language = _base_language(chapter["target_language"])
        sample_mask = _eligible_baseline_mask(frame) & _same_pair_mask(
            frame, source_language, target_language
        )
        sample = pd.to_numeric(
            frame.loc[sample_mask, "length_ratio"], errors="coerce"
        ).to_numpy(dtype=np.float64)
        sample = sample[np.isfinite(sample)]
        sample_size = int(sample.size)
        language_pair = (source_language, target_language)

        if sample_size < MIN_BASELINE_SAMPLE_SIZE:
            return BookRatioBaseline(
                language_pair=language_pair,
                sample_size=sample_size,
                median=None,
                scale=None,
                scale_method=None,
            )

        median = float(np.median(sample))
        mad = float(np.median(np.abs(sample - median)))
        if mad > 0.0:
            return BookRatioBaseline(
                language_pair=language_pair,
                sample_size=sample_size,
                median=median,
                scale=1.4826 * mad,
                scale_method="mad",
            )

        q1, q3 = np.quantile(sample, [0.25, 0.75])
        return BookRatioBaseline(
            language_pair=language_pair,
            sample_size=sample_size,
            median=median,
            scale=float((q3 - q1) / 1.349),
            scale_method="iqr",
        )

    def classify_ratio_risk(self, frame: pd.DataFrame, chapter_id: str) -> RatioRisk:
        """Classify absolute language-profile and relative book-level ratio risks."""
        chapter = _chapter_row(frame, chapter_id)
        source_language = _base_language(chapter["source_language"])
        target_language = _base_language(chapter["target_language"])
        ratio = _finite_ratio(chapter["length_ratio"])
        baseline = self.ratio_baseline(frame, chapter_id)
        absolute_profile, within_absolute_profile = _absolute_profile_status(
            source_language, target_language, ratio
        )
        robust_z = _robust_z(ratio, baseline)
        relative_risk = _classify_relative_risk(robust_z)

        return RatioRisk(
            baseline=baseline,
            absolute_profile=absolute_profile,
            within_absolute_profile=within_absolute_profile,
            robust_z=robust_z,
            relative_risk=relative_risk,
            requires_deep_check=(
                within_absolute_profile is False
                or relative_risk in {RelativeRisk.MEDIUM, RelativeRisk.HIGH}
            ),
        )


def _base_language_series(values: pd.Series) -> pd.Series:
    return (
        values.astype("string")
        .str.strip()
        .str.lower()
        .str.replace("_", "-", regex=False)
        .str.split("-", n=1)
        .str[0]
    )


def _base_language(value: object) -> str:
    return str(value).strip().lower().replace("_", "-").split("-", 1)[0]


def _chapter_row(frame: pd.DataFrame, chapter_id: str) -> pd.Series:
    matches = frame.loc[frame["chapter_id"].eq(chapter_id)]
    if matches.empty:
        raise KeyError(f"unknown chapter_id: {chapter_id}")
    if len(matches) != 1:
        raise ValueError(f"duplicate chapter_id: {chapter_id}")
    return matches.iloc[0]


def _eligible_baseline_mask(frame: pd.DataFrame) -> pd.Series:
    content_kind = frame["content_kind"].astype("string")
    source_chars = pd.to_numeric(frame["source_chars"], errors="coerce")
    risk_level = frame["risk_level"].astype("string")
    return (
        content_kind.eq("narrative")
        & source_chars.ge(MIN_BASELINE_SOURCE_CHARS)
        & risk_level.ne("failed")
    ).fillna(False)


def _same_pair_mask(
    frame: pd.DataFrame, source_language: str, target_language: str
) -> pd.Series:
    return _base_language_series(frame["source_language"]).eq(
        source_language
    ) & _base_language_series(frame["target_language"]).eq(target_language)


def _finite_ratio(value: object) -> float | None:
    try:
        ratio = float(value)
    except (TypeError, ValueError):
        return None
    return ratio if np.isfinite(ratio) else None


def _absolute_profile_status(
    source_language: str, target_language: str, ratio: float | None
) -> tuple[str, bool | None]:
    try:
        profile = get_ratio_profile(source_language, target_language)
    except KeyError:
        return "unsupported", None
    if ratio is None:
        return profile.key, None
    return profile.key, profile.contains(ratio)


def _robust_z(ratio: float | None, baseline: BookRatioBaseline) -> float | None:
    if ratio is None or baseline.median is None or baseline.scale is None:
        return None
    if not np.isfinite(baseline.median) or not np.isfinite(baseline.scale):
        return None
    if baseline.scale > 0.0:
        result = (ratio - baseline.median) / baseline.scale
        return float(result) if np.isfinite(result) else None
    if ratio == baseline.median:
        return 0.0
    return math.copysign(math.inf, ratio - baseline.median)


def _classify_relative_risk(robust_z: float | None) -> RelativeRisk:
    if robust_z is None or math.isnan(robust_z):
        return RelativeRisk.UNAVAILABLE
    magnitude = abs(robust_z)
    if magnitude >= HIGH_ROBUST_Z_THRESHOLD:
        return RelativeRisk.HIGH
    if magnitude >= MEDIUM_ROBUST_Z_THRESHOLD:
        return RelativeRisk.MEDIUM
    return RelativeRisk.LOW
