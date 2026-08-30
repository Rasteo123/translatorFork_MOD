# -*- coding: utf-8 -*-
"""Read-only Qt models over one immutable snapshot of the QA journal."""

from __future__ import annotations

from dataclasses import dataclass, field

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt

from ....qa.book_metrics import BookMetricsAnalyzer, RelativeRisk
from ....qa.models import ChapterMetrics, RiskLevel


RISK_LABELS = {
    RiskLevel.LOW: "Низкий",
    RiskLevel.MEDIUM: "Средний",
    RiskLevel.HIGH: "Высокий",
    RiskLevel.FAILED: "Сбой",
}
DECISION_LABELS = {
    "fixed": "Исправлено",
    "repair_rejected": "Исправление отклонено",
    "warning": "Предупреждение",
    "hallucinated_addition": "Добавленный факт",
    "no_gap": "Пропусков нет",
    "missing_content": "Потерян фрагмент",
    "covered": "Смысл передан",
    "intentional_foreign": "Намеренный иностранный текст",
    "ambiguous": "Неоднозначно",
    "excluded": "Исключено",
    "error": "Ошибка",
    "cancelled": "Отменено",
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
        )


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
    from ....qa.ratio_profiles import get_ratio_profile

    try:
        profile = get_ratio_profile(metrics.source_language, metrics.target_language)
    except KeyError:
        return "профиль не задан"
    inside = profile.contains(metrics.length_ratio)
    bounds = f"{profile.minimum:.2f}–{profile.maximum:.2f}"
    return f"{'в профиле' if inside else 'вне профиля'} {bounds}"


class ChapterQaTableModel(QAbstractTableModel):
    """Table over a snapshot; it never touches a live DataFrame or the journal."""

    COLUMNS = (
        ("Глава", "chapter_id"),
        ("Риск", "risk_label"),
        ("Языковая пара", "language_pair"),
        ("Коэффициент", "length_ratio"),
        ("Профиль длины", "profile_status"),
        ("Книжная норма", "book_position"),
        ("Конфликты терминов", "glossary_conflicts"),
        ("Остатки исходника", "untranslated_fragments"),
        ("Возможные пропуски", "possible_gaps"),
        ("Подтверждённые", "confirmed_gaps"),
        ("Языковые дефекты", "language_issues"),
        ("Исправлено", "applied_repairs"),
        ("Время, с", "duration_seconds"),
        ("Токены", "tokens"),
    )

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._snapshot = BookQaReportSnapshot()

    def set_snapshot(self, snapshot: BookQaReportSnapshot) -> None:
        """Replace the whole report; the model owns no mutable state of its own."""
        if not isinstance(snapshot, BookQaReportSnapshot):
            raise TypeError("snapshot must be a BookQaReportSnapshot")
        self.beginResetModel()
        self._snapshot = snapshot
        self.endResetModel()

    @property
    def snapshot(self) -> BookQaReportSnapshot:
        return self._snapshot

    def row_at(self, row: int) -> ChapterQaRow | None:
        if 0 <= row < len(self._snapshot.rows):
            return self._snapshot.rows[row]
        return None

    def row_for_chapter(self, chapter_id: str) -> int:
        for index, row in enumerate(self._snapshot.rows):
            if row.chapter_id == chapter_id:
                return index
        return -1

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802 - Qt API
        return 0 if parent.isValid() else len(self._snapshot.rows)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802 - Qt API
        return 0 if parent.isValid() else len(self.COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal and 0 <= section < len(self.COLUMNS):
            return self.COLUMNS[section][0]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = self.row_at(index.row())
        if row is None:
            return None
        field_name = self.COLUMNS[index.column()][1]
        if role == Qt.ItemDataRole.DisplayRole:
            if field_name == "risk_label" and row.blocked_reason:
                # Colour is never the only signal: a blocked chapter says so.
                return f"⛔ {row.risk_label} — перевод остановлен"
            return _format(getattr(row, field_name))
        if role == Qt.ItemDataRole.ToolTipRole and row.blocked_reason:
            return f"Перевод остановлен: {row.blocked_reason}"
        return None


def _format(value: object) -> str:
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)
