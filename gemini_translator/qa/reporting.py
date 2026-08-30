"""Turn the durable QA journal into tables a person can open anywhere."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import os
from pathlib import Path

import pandas as pd

from .capabilities import QaCapabilityKey
from .journal import QaJournal
from .models import ChapterMetrics
from .report_snapshot import BookQaReportSnapshot


SCRIPT_COLUMNS = {
    "han": "untranslated_han",
    "kana": "untranslated_kana",
    "hangul": "untranslated_hangul",
    "latin": "untranslated_latin",
}
DURATION_COLUMNS = {
    QaCapabilityKey.RAZDEL: "duration_razdel",
    QaCapabilityKey.LANGUAGE_TOOL: "duration_language_tool",
    QaCapabilityKey.SLOVNET: "duration_slovnet",
    QaCapabilityKey.COMETKIWI: "duration_cometkiwi",
}
CHAPTER_COLUMNS = (
    "chapter",
    "source_language",
    "target_language",
    "content_kind",
    "source_chars",
    "translated_chars",
    "ratio",
    "glossary_hits",
    "glossary_conflicts",
    *SCRIPT_COLUMNS.values(),
    "untranslated_total",
    "language_tool_issues",
    "protected_entities",
    "syntax_candidates",
    "quality_estimator",
    "quality_score",
    "quality_score_status",
    *DURATION_COLUMNS.values(),
    "retries",
    "input_tokens",
    "output_tokens",
    "duration",
    "risk",
    "actions",
)
CSV_ENCODING = "utf-8-sig"


class QaReportBuilder:
    """Build the book's report from the journal, without ever mutating it.

    Frames are derived on demand: the JSON journal stays the single source of
    truth, and no pandas-specific value is ever written back into it.
    """

    def chapter_frame(self, journal: QaJournal) -> pd.DataFrame:
        """One row per chapter, with every count the spec names."""
        records = [
            _chapter_record(journal.metrics[chapter_id])
            for chapter_id in sorted(journal.metrics)
        ]
        frame = pd.DataFrame.from_records(records, columns=CHAPTER_COLUMNS)
        if frame.empty:
            return frame
        for column in ("source_language", "target_language", "content_kind", "risk"):
            frame[column] = frame[column].astype("category")
        for column in (
            "source_chars",
            "translated_chars",
            "glossary_hits",
            "glossary_conflicts",
            *SCRIPT_COLUMNS.values(),
            "untranslated_total",
            "language_tool_issues",
            "protected_entities",
            "syntax_candidates",
            "retries",
            "input_tokens",
            "output_tokens",
        ):
            frame[column] = frame[column].astype("Int64")
        for column in ("ratio", "quality_score", "duration", *DURATION_COLUMNS.values()):
            frame[column] = frame[column].astype("Float64")
        return frame

    def glossary_frame(self, journal: QaJournal) -> pd.DataFrame:
        """One row per observed term translation, with how often it was seen."""
        records = [
            {
                "original_term": observation.original_term,
                "observed_translation": observation.observed_translation,
                "chapter": observation.chapter_id,
                "occurrences": observation.occurrences,
            }
            for observation in journal.glossary_observations
        ]
        frame = pd.DataFrame.from_records(
            records,
            columns=("original_term", "observed_translation", "chapter", "occurrences"),
        )
        if frame.empty:
            return frame
        frame["occurrences"] = frame["occurrences"].astype("Int64")
        grouped = (
            frame.groupby(["original_term", "observed_translation"], as_index=False)
            .agg(
                occurrences=("occurrences", "sum"),
                chapters=("chapter", "nunique"),
            )
            .sort_values(
                ["original_term", "occurrences"], ascending=[True, False]
            )
            .reset_index(drop=True)
        )
        variants = (
            grouped.groupby("original_term", as_index=False)
            .agg(variants=("observed_translation", "nunique"))
        )
        return grouped.merge(variants, on="original_term", how="left")

    def candidate_frame(self, journal: QaJournal) -> pd.DataFrame:
        """One row per recorded decision, in the order they were made."""
        return _entry_frame(journal.candidates, ("entry_id", "chapter_id", "decision"))

    def repair_frame(self, journal: QaJournal) -> pd.DataFrame:
        """One row per applied repair, enough to find it again in the book."""
        return _entry_frame(
            journal.repairs,
            ("patch_id", "chapter_id", "candidate_id", "session_id", "applied_at"),
        )

    def summary(self, journal: QaJournal, open_gates=()) -> BookQaReportSnapshot:
        """Return the same snapshot the report window shows."""
        return BookQaReportSnapshot.from_journal(journal, open_gates)

    def export_csv_bundle(self, directory: Path | str, journal: QaJournal) -> tuple[Path, ...]:
        """Write four CSV files that open anywhere, replacing them atomically."""
        target = Path(directory)
        target.mkdir(parents=True, exist_ok=True)
        frames = (
            ("chapters.csv", self.chapter_frame(journal)),
            ("glossary.csv", self.glossary_frame(journal)),
            ("candidates.csv", self.candidate_frame(journal)),
            ("repairs.csv", self.repair_frame(journal)),
        )
        written: list[Path] = []
        for name, frame in frames:
            path = target / name
            temporary = path.with_name(path.name + ".tmp")
            frame.to_csv(temporary, index=False, encoding=CSV_ENCODING)
            os.replace(temporary, path)
            written.append(path)
        return tuple(written)


def _entry_frame(entries: Iterable[Mapping[str, object]], columns) -> pd.DataFrame:
    records = [
        {column: entry.get(column) for column in columns}
        for entry in entries
        if isinstance(entry, Mapping)
    ]
    return pd.DataFrame.from_records(records, columns=list(columns))


def _chapter_record(metrics: ChapterMetrics) -> dict[str, object]:
    scripts = dict(metrics.untranslated_by_script or {})
    durations = dict(metrics.capability_durations or {})
    record: dict[str, object] = {
        "chapter": metrics.chapter_id,
        "source_language": metrics.source_language,
        "target_language": metrics.target_language,
        "content_kind": metrics.content_kind,
        "source_chars": metrics.source_chars,
        "translated_chars": metrics.translated_chars,
        "ratio": metrics.length_ratio,
        "glossary_hits": metrics.glossary_matched,
        "glossary_conflicts": metrics.glossary_conflicts,
        "untranslated_total": sum(int(value) for value in scripts.values()),
        "language_tool_issues": metrics.language_tool_issues,
        "protected_entities": metrics.protected_entities,
        "syntax_candidates": metrics.syntax_candidates,
        "quality_estimator": metrics.quality_estimator,
        "quality_score": metrics.quality_score,
        "quality_score_status": metrics.quality_score_status,
        "retries": metrics.retries,
        "input_tokens": metrics.input_tokens,
        "output_tokens": metrics.output_tokens,
        "duration": metrics.duration_seconds,
        "risk": str(metrics.risk_level),
        "actions": ", ".join(str(action) for action in metrics.applied_actions),
    }
    for script, column in SCRIPT_COLUMNS.items():
        record[column] = int(scripts.get(script, 0) or 0)
    for key, column in DURATION_COLUMNS.items():
        value = durations.get(key, durations.get(str(key)))
        record[column] = float(value) if isinstance(value, (int, float)) else None
    return record
