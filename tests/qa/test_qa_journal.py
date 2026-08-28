import json

import pytest

from gemini_translator.qa.journal import (
    QaJournal,
    QaJournalCorruptedError,
    QaJournalUnsupportedVersionError,
)
from gemini_translator.qa.models import ChapterMetrics
from gemini_translator.utils.project_manager import TranslationProjectManager


def _metrics() -> ChapterMetrics:
    return ChapterMetrics(
        chapter_id="chapter-1",
        source_language="zh",
        target_language="ru",
        content_kind="narrative",
        source_chars=1000,
        translated_chars=2800,
        source_units=40,
        aligned_units=39,
        possible_gaps=1,
        glossary_expected=5,
        glossary_matched=5,
        glossary_conflicts=0,
        untranslated_by_script={"han": 0},
        allowed_foreign_fragments=1,
        language_tool_issues=0,
        protected_entities=0,
        syntax_candidates=0,
        quality_estimator=None,
        quality_score=None,
        quality_score_status="not_run",
        capability_durations={},
        retries=0,
        input_tokens=100,
        output_tokens=300,
        duration_seconds=4.5,
        risk_level="low",
        applied_actions=(),
    )


def test_journal_round_trip_uses_v1_json_and_dataframe_schema(tmp_path):
    """Removing v1 persistence or metric serialization breaks a restored report."""
    journal = QaJournal.empty(book_id="book-1")
    journal.upsert_metrics(_metrics())
    path = tmp_path / "translation_qa.json"

    journal.save(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    restored = QaJournal.load(path)

    assert payload["schema_version"] == 1
    assert payload["book_id"] == "book-1"
    assert set(payload) == {
        "schema_version",
        "book_id",
        "updated_at",
        "metrics",
        "candidates",
        "repairs",
        "glossary_observations",
    }
    assert restored.metrics["chapter-1"].length_ratio == 2.8
    assert list(restored.metrics_frame().columns) == list(
        ChapterMetrics.dataframe_columns()
    )
    assert restored.to_frame().equals(restored.metrics_frame())


@pytest.mark.parametrize(
    ("payload", "error_type"),
    [
        ("{not json", QaJournalCorruptedError),
        (json.dumps({"schema_version": 99}), QaJournalUnsupportedVersionError),
    ],
)
def test_journal_load_rejects_bad_data_without_rewriting_it(
    tmp_path, payload, error_type
):
    """Turning bad journals into empty files would erase the user's QA history."""
    path = tmp_path / "translation_qa.json"
    path.write_text(payload, encoding="utf-8")

    with pytest.raises(error_type):
        QaJournal.load(path)

    assert path.read_text(encoding="utf-8") == payload


def test_project_manager_uses_project_folder_for_qa_history_paths(tmp_path):
    """Using a cache path or a nonexistent project_dir loses durable QA history."""
    manager = TranslationProjectManager(str(tmp_path))

    assert manager.get_translation_qa_journal_path() == tmp_path / "translation_qa.json"
    assert manager.get_translation_qa_backup_dir() == tmp_path / "translation_qa_backups"

