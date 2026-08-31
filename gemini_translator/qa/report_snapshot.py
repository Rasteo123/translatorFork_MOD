"""One immutable, Qt-free view of the QA journal, ready to be shown or exported."""

from __future__ import annotations

from dataclasses import dataclass, field

from .book_metrics import BookMetricsAnalyzer, RelativeRisk
from .models import ChapterMetrics, RiskLevel


RISK_LABELS = {
    RiskLevel.LOW: "Низкий",
    RiskLevel.MEDIUM: "Средний",
    RiskLevel.HIGH: "Высокий",
    RiskLevel.FAILED: "Сбой",
}
RELATIVE_RISK_LABELS = {
    RelativeRisk.UNAVAILABLE: "нет книжной нормы",
    RelativeRisk.LOW: "в норме книги",
    RelativeRisk.MEDIUM: "отклонение",
    RelativeRisk.HIGH: "сильное отклонение",
}


@dataclass(frozen=True, slots=True)
class ChapterQaRow:
    """One chapter as the report shows it, already formatted for reading."""

    chapter_id: str
    language_pair: str
    length_ratio: float
    profile_status: str
    book_position: str
    glossary_conflicts: int
    untranslated_fragments: int
    possible_gaps: int
    confirmed_gaps: int
    language_issues: int
    applied_repairs: int
    risk_label: str
    risk_level: str
    duration_seconds: float
    tokens: int
    blocked_reason: str = ""


@dataclass(frozen=True, slots=True)
class BookQaReportSnapshot:
    """An immutable view of the journal, safe to hand to the UI thread."""

    rows: tuple[ChapterQaRow, ...] = ()
    decisions_by_chapter: dict[str, tuple[str, ...]] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    limited_mode_chapters: tuple[str, ...] = ()

    @property
    def blocked_chapters(self) -> tuple[str, ...]:
        return tuple(row.chapter_id for row in self.rows if row.blocked_reason)

    @property
    def repaired_chapters(self) -> tuple[str, ...]:
        return tuple(row.chapter_id for row in self.rows if row.applied_repairs)

    @classmethod
    def from_journal(cls, journal, open_gates=()) -> "BookQaReportSnapshot":
        """Build the snapshot from a loaded journal and the queue's open gates."""
        gate_reasons = {
            str(getattr(gate, "chapter_id", "")): str(getattr(gate, "reason", ""))
            or "неустранённый риск"
            for gate in open_gates or ()
        }
        metrics = [
            journal.metrics[chapter_id] for chapter_id in sorted(journal.metrics)
        ]
        decisions: dict[str, list[str]] = {}
        for entry in getattr(journal, "candidates", ()):
            chapter_id = str(entry.get("chapter_id", ""))
            decision = str(entry.get("decision", ""))
            if chapter_id and decision:
                decisions.setdefault(chapter_id, []).append(decision)
        repairs_by_chapter: dict[str, int] = {}
        for repair in getattr(journal, "repairs", ()):
            chapter_id = str(repair.get("chapter_id", ""))
            if chapter_id:
                repairs_by_chapter[chapter_id] = repairs_by_chapter.get(chapter_id, 0) + 1

        positions = _book_positions(metrics)
        rows = tuple(
            _row_for(
                item,
                decisions.get(item.chapter_id, ()),
                repairs_by_chapter.get(item.chapter_id, 0),
                positions.get(item.chapter_id, "нет книжной нормы"),
                gate_reasons.get(item.chapter_id, ""),
            )
            for item in metrics
        )
        return cls(
            rows=rows,
            decisions_by_chapter={
                chapter_id: tuple(values) for chapter_id, values in decisions.items()
            },
            limited_mode_chapters=tuple(
                item.chapter_id for item in metrics if _checked_without_alignment(item)
            ),
        )


def _checked_without_alignment(metrics: ChapterMetrics) -> bool:
    """Report whether this chapter was checked without semantic comparison.

    The journal keeps no mode field, but it does not need one: a chapter that
    was aligned has aligned units, and a chapter checked in limited mode has
    source units and none aligned.  The report's counter for this has existed
    from the start and was never filled.
    """
    return metrics.source_units > 0 and metrics.aligned_units == 0


def _book_positions(metrics: list[ChapterMetrics]) -> dict[str, str]:
    """Describe each chapter against its own language pair, when that is known."""
    if not metrics:
        return {}
    try:
        analyzer = BookMetricsAnalyzer()
        frame = analyzer.analyze(metrics)
        if frame.empty:
            return {}
        positions: dict[str, str] = {}
        for item in metrics:
            risk = analyzer.classify_ratio_risk(frame, item.chapter_id)
            if risk.baseline.median is None:
                positions[item.chapter_id] = "нет книжной нормы"
                continue
            z_value = risk.robust_z
            label = RELATIVE_RISK_LABELS.get(risk.relative_risk, "")
            positions[item.chapter_id] = (
                f"медиана {risk.baseline.median:.2f}, z={z_value:.1f} ({label})"
                if z_value is not None
                else f"медиана {risk.baseline.median:.2f}"
            )
        return positions
    except Exception:  # noqa: BLE001 - a report must never fail on statistics
        return {}


def _row_for(
    metrics: ChapterMetrics,
    decisions: tuple[str, ...],
    applied_repairs: int,
    book_position: str,
    blocked_reason: str,
) -> ChapterQaRow:
    untranslated = metrics.untranslated_by_script or {}
    risk = RiskLevel(metrics.risk_level) if metrics.risk_level else RiskLevel.LOW
    return ChapterQaRow(
        chapter_id=metrics.chapter_id,
        language_pair=f"{metrics.source_language} → {metrics.target_language}",
        length_ratio=metrics.length_ratio,
        profile_status=_profile_status(metrics),
        book_position=book_position,
        glossary_conflicts=metrics.glossary_conflicts,
        untranslated_fragments=sum(int(value) for value in untranslated.values()),
        possible_gaps=metrics.possible_gaps,
        confirmed_gaps=sum(
            1 for decision in decisions if decision in {"fixed", "repair_rejected"}
        ),
        language_issues=metrics.language_tool_issues,
        applied_repairs=applied_repairs,
        risk_label=RISK_LABELS.get(risk, str(risk)),
        risk_level=str(risk),
        duration_seconds=metrics.duration_seconds,
        tokens=metrics.input_tokens + metrics.output_tokens,
        blocked_reason=blocked_reason,
    )


def _profile_status(metrics: ChapterMetrics) -> str:
    from .ratio_profiles import get_ratio_profile

    try:
        profile = get_ratio_profile(metrics.source_language, metrics.target_language)
    except KeyError:
        return "профиль не задан"
    inside = profile.contains(metrics.length_ratio)
    bounds = f"{profile.minimum:.2f}–{profile.maximum:.2f}"
    return f"{'в профиле' if inside else 'вне профиля'} {bounds}"


