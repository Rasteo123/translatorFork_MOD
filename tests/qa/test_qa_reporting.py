"""The exported report must match the journal exactly, and open anywhere."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from gemini_translator.qa.capabilities import QaCapabilityKey
from gemini_translator.qa.journal import QaJournal
from gemini_translator.qa.models import (
    Action,
    ChapterMetrics,
    GlossaryObservation,
    GlossaryPolicy,
    QaJournalEntry,
    RiskLevel,
)
from gemini_translator.qa.reporting import CHAPTER_COLUMNS, QaReportBuilder


def _metrics(chapter_id: str, **overrides) -> ChapterMetrics:
    values = {
        "chapter_id": chapter_id,
        "source_language": "zh",
        "target_language": "ru",
        "content_kind": "narrative",
        "source_chars": 1000,
        "translated_chars": 2900,
        "source_units": 40,
        "aligned_units": 39,
        "possible_gaps": 1,
        "glossary_expected": 5,
        "glossary_matched": 4,
        "glossary_conflicts": 1,
        "untranslated_by_script": {"han": 2, "latin": 1},
        "language_tool_issues": 3,
        "protected_entities": 2,
        "syntax_candidates": 1,
        "quality_estimator": "cometkiwi",
        "quality_score": 0.82,
        "quality_score_status": "completed",
        "capability_durations": {
            QaCapabilityKey.RAZDEL: 0.4,
            QaCapabilityKey.LANGUAGE_TOOL: 1.25,
        },
        "retries": 1,
        "input_tokens": 5000,
        "output_tokens": 1800,
        "duration_seconds": 12.5,
        "risk_level": RiskLevel.MEDIUM,
        "applied_actions": (Action.REPAIR_APPLIED,),
    }
    values.update(overrides)
    return ChapterMetrics(**values)  # type: ignore[arg-type]


def _journal() -> QaJournal:
    journal = QaJournal.empty(book_id="book-1")
    journal.upsert_metrics(_metrics("chapter-1"))
    journal.upsert_metrics(_metrics("chapter-2", risk_level=RiskLevel.LOW))
    journal.append(
        QaJournalEntry(entry_id="e1", chapter_id="chapter-1", decision="fixed")
    )
    journal.append_repair(
        {
            "patch_id": "p1",
            "chapter_id": "chapter-1",
            "candidate_id": "gap-1",
            "session_id": "session-1",
            "applied_at": "2026-08-30T10:00:00+00:00",
        }
    )
    for chapter_id, translation, occurrences in (
        ("chapter-1", "Зал Духов", 3),
        ("chapter-2", "Зал Духов", 2),
        ("chapter-2", "Храм Духов", 1),
    ):
        journal.append_glossary_observation(
            GlossaryObservation(
                original_term="武魂殿",
                observed_translation=translation,
                canonical_translation="Зал Духов",
                morphology_signature=("зал", "дух"),
                morphology_confidence="high",
                chapter_id=chapter_id,
                occurrences=occurrences,
                policy=GlossaryPolicy.MUST_TRANSLATE,
            )
        )
    return journal


def test_chapter_frame_carries_every_documented_column():
    """A missing column silently drops a signal the user was promised."""
    frame = QaReportBuilder().chapter_frame(_journal())

    assert list(frame.columns) == list(CHAPTER_COLUMNS)
    assert len(frame) == 2
    row = frame.loc[frame["chapter"] == "chapter-1"].iloc[0]
    assert row["ratio"] == pytest.approx(2.9)
    assert row["glossary_hits"] == 4
    assert row["language_tool_issues"] == 3
    assert row["risk"] == "medium"
    assert row["actions"] == "repair_applied"


def test_untranslated_scripts_become_stable_columns():
    """A per-script dictionary cannot be filtered or summed in a spreadsheet."""
    row = QaReportBuilder().chapter_frame(_journal()).iloc[0]

    assert row["untranslated_han"] == 2
    assert row["untranslated_latin"] == 1
    assert row["untranslated_kana"] == 0
    assert row["untranslated_hangul"] == 0
    assert row["untranslated_total"] == 3


def test_capability_durations_become_four_nullable_columns():
    """An analyzer that never ran must be empty, not zero."""
    row = QaReportBuilder().chapter_frame(_journal()).iloc[0]

    assert row["duration_razdel"] == pytest.approx(0.4)
    assert row["duration_language_tool"] == pytest.approx(1.25)
    assert pd.isna(row["duration_slovnet"])
    assert pd.isna(row["duration_cometkiwi"])


def test_an_empty_journal_produces_empty_frames_not_errors():
    """A book checked for the first time must still export a valid report."""
    builder = QaReportBuilder()
    journal = QaJournal.empty(book_id="book-1")

    assert list(builder.chapter_frame(journal).columns) == list(CHAPTER_COLUMNS)
    assert builder.chapter_frame(journal).empty
    assert builder.glossary_frame(journal).empty
    assert builder.candidate_frame(journal).empty
    assert builder.repair_frame(journal).empty


def test_glossary_frame_aggregates_variants_per_term():
    """A term translated two ways is exactly what the report must surface."""
    frame = QaReportBuilder().glossary_frame(_journal())

    rows = frame.set_index("observed_translation")
    assert rows.loc["Зал Духов", "occurrences"] == 5
    assert rows.loc["Зал Духов", "chapters"] == 2
    assert rows.loc["Храм Духов", "occurrences"] == 1
    assert set(frame["variants"]) == {2}


def test_decisions_and_repairs_export_their_own_tables():
    """A journal entry is only auditable if it survives the export."""
    builder = QaReportBuilder()
    journal = _journal()

    candidates = builder.candidate_frame(journal)
    repairs = builder.repair_frame(journal)

    assert list(candidates["decision"]) == ["fixed"]
    assert list(repairs["patch_id"]) == ["p1"]
    assert list(repairs["chapter_id"]) == ["chapter-1"]


def test_the_csv_bundle_reloads_with_the_same_rows(tmp_path: Path):
    """An export nobody can read back is not a report."""
    journal = _journal()
    builder = QaReportBuilder()

    written = builder.export_csv_bundle(tmp_path, journal)

    assert [path.name for path in written] == [
        "chapters.csv",
        "glossary.csv",
        "candidates.csv",
        "repairs.csv",
    ]
    reloaded = pd.read_csv(tmp_path / "chapters.csv")
    assert list(reloaded["chapter"]) == ["chapter-1", "chapter-2"]
    assert len(reloaded) == len(builder.chapter_frame(journal))
    assert not list(tmp_path.glob("*.tmp"))


def test_export_replaces_a_previous_bundle_in_place(tmp_path: Path):
    """Re-exporting must not leave two half-written versions side by side."""
    builder = QaReportBuilder()
    builder.export_csv_bundle(tmp_path, _journal())
    smaller = QaJournal.empty(book_id="book-1")
    smaller.upsert_metrics(_metrics("chapter-9"))

    builder.export_csv_bundle(tmp_path, smaller)

    reloaded = pd.read_csv(tmp_path / "chapters.csv")
    assert list(reloaded["chapter"]) == ["chapter-9"]


def test_csv_is_written_so_a_spreadsheet_shows_russian_correctly(tmp_path: Path):
    """A report full of mojibake is worse than no report."""
    QaReportBuilder().export_csv_bundle(tmp_path, _journal())

    raw = (tmp_path / "glossary.csv").read_bytes()

    assert raw.startswith(b"\xef\xbb\xbf")
    assert "Зал Духов" in raw.decode("utf-8-sig")


def test_the_journal_is_never_modified_by_reporting():
    """The report is derived; the decision history must stay exactly as it was."""
    journal = _journal()
    before = journal.updated_at
    builder = QaReportBuilder()

    builder.chapter_frame(journal)
    builder.glossary_frame(journal)
    builder.summary(journal)

    assert journal.updated_at == before
    assert len(journal.metrics) == 2
