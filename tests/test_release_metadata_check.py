from pathlib import Path

from gemini_translator.scripts.check_release_metadata import check_release_metadata


def test_release_metadata_check_accepts_current_project():
    assert check_release_metadata() == []


def test_release_metadata_check_reports_missing_release_notes(tmp_path: Path):
    errors = check_release_metadata(tmp_path)

    assert errors == ["No RELEASE_NOTES_v*.md files found."]
