# -*- coding: utf-8 -*-
"""Read-only Qt models over one immutable snapshot of the QA journal."""

from __future__ import annotations

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt

from ....qa.report_snapshot import (
    RELATIVE_RISK_LABELS,
    RISK_LABELS,
    BookQaReportSnapshot,
    ChapterQaRow,
)


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

__all__ = (
    "BookQaReportSnapshot",
    "ChapterQaRow",
    "ChapterQaTableModel",
    "DECISION_LABELS",
    "RELATIVE_RISK_LABELS",
    "RISK_LABELS",
)


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
