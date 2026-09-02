"""The quality section must show the report and never act on a stale selection."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6 import QtWidgets

from gemini_translator.qa.capabilities import QaCapabilitySettings
from gemini_translator.qa.journal import QaJournal
from gemini_translator.qa.models import ChapterMetrics, QaJournalEntry, RiskLevel
from gemini_translator.qa.settings import QaSettings
from gemini_translator.ui.dialogs.validation_dialogs import (
    BookQaReportSnapshot,
    ChapterQaTableModel,
    TranslationQualityDialog,
)


@pytest.fixture(scope="module")
def qt_app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _journal(repaired: bool = True) -> QaJournal:
    journal = QaJournal.empty(book_id="book-1")
    for index in range(6):
        journal.upsert_metrics(
            ChapterMetrics(
                chapter_id=f"chapter-{index}",
                source_language="zh",
                target_language="ru",
                source_chars=1000,
                translated_chars=2900 + index * 20,
                possible_gaps=index % 2,
                glossary_conflicts=index % 3,
                risk_level=RiskLevel.MEDIUM if index % 2 else RiskLevel.LOW,
            )
        )
    if repaired:
        journal.append(
            QaJournalEntry(entry_id="e1", chapter_id="chapter-1", decision="fixed")
        )
        journal.append_repair({"patch_id": "p1", "chapter_id": "chapter-1"})
    return journal


class _Gate:
    def __init__(self, chapter_id: str, reason: str) -> None:
        self.chapter_id = chapter_id
        self.reason = reason


def _dialog(qt_app, **kwargs) -> TranslationQualityDialog:
    dialog = TranslationQualityDialog(**kwargs)
    dialog.set_report(BookQaReportSnapshot.from_journal(_journal()))
    return dialog


def test_dialog_exposes_the_four_actions(qt_app):
    """The user must find exactly the four documented actions, by name."""
    dialog = _dialog(qt_app)

    assert dialog.check_chapter_button.text() == "Проверить и исправить главу"
    assert dialog.check_all_button.text() == "Проверить и исправить все главы"
    assert dialog.undo_chapter_button.text() == "Отменить исправления главы"
    assert (
        dialog.undo_all_button.text()
        == "Отменить все автоматические исправления"
    )


def test_a_running_check_disables_conflicting_actions(qt_app):
    """Two overlapping passes over one book would fight over the same files."""
    dialog = _dialog(qt_app)
    dialog.select_chapter("chapter-1")

    dialog.set_busy(True)
    assert not dialog.check_all_button.isEnabled()
    assert not dialog.check_chapter_button.isEnabled()
    assert not dialog.undo_all_button.isEnabled()
    assert dialog.cancel_button.isEnabled()

    dialog.set_busy(False)
    assert dialog.check_all_button.isEnabled()
    assert dialog.check_chapter_button.isEnabled()
    assert not dialog.cancel_button.isEnabled()


def test_actions_require_the_state_they_act_on(qt_app):
    """Undo must be offered only where an automatic repair actually exists."""
    dialog = _dialog(qt_app)

    dialog.select_chapter("chapter-0")
    assert dialog.check_chapter_button.isEnabled()
    assert not dialog.undo_chapter_button.isEnabled()

    dialog.select_chapter("chapter-1")
    assert dialog.undo_chapter_button.isEnabled()


def test_selecting_a_chapter_shows_its_decisions(qt_app):
    """A row is only useful with the reasoning behind it."""
    dialog = _dialog(qt_app)

    dialog.select_chapter("chapter-1")
    details = dialog.details.toPlainText()

    assert "chapter-1" in details
    assert "zh → ru" in details
    assert "Исправлено" in details


def test_report_summary_names_blocked_chapters(qt_app):
    """A stopped translation must be visible without opening a row."""
    dialog = _dialog(qt_app)
    snapshot = BookQaReportSnapshot.from_journal(
        _journal(), open_gates=[_Gate("chapter-3", "подтверждённый пропуск")]
    )

    dialog.set_report(snapshot)

    assert snapshot.blocked_chapters == ("chapter-3",)
    assert "chapter-3" in dialog.summary_label.text()
    dialog.select_chapter("chapter-3")
    assert "подтверждённый пропуск" in dialog.details.toPlainText()


def test_check_chapter_emits_the_selected_chapter(qt_app):
    """The action must apply to what the user has selected, or to nothing."""
    dialog = _dialog(qt_app)
    seen: list[str] = []
    dialog.check_chapter_requested.connect(seen.append)

    dialog._request_check_chapter()
    assert seen == []

    dialog.select_chapter("chapter-2")
    dialog._request_check_chapter()
    assert seen == ["chapter-2"]


def test_embedding_provider_choice_offers_its_own_models_and_key(qt_app):
    """The user picks the embedding key and model, not the translation session."""
    dialog = _dialog(
        qt_app,
        api_keys=[
            {"key": "AIzaSy-gemini-key-value", "provider": "gemini"},
            {"key": "sk-openai-key-value", "provider": "openai"},
        ],
    )

    dialog.embedding_provider_combo.setCurrentIndex(
        dialog.embedding_provider_combo.findData("openai_compatible")
    )
    models = [
        dialog.embedding_model_combo.itemText(index)
        for index in range(dialog.embedding_model_combo.count())
    ]
    assert "text-embedding-3-small" in models
    assert dialog.embedding_base_url_edit.isEnabled()

    dialog.embedding_key_combo.setCurrentIndex(
        dialog.embedding_key_combo.findData("sk-openai-key-value")
    )
    dialog.embedding_base_url_edit.setText("https://api.openai.com/v1")
    dialog.embedding_model_combo.setEditText("text-embedding-3-large")
    settings = dialog.qa_settings()

    assert settings.embedding_provider == "openai_compatible"
    assert settings.embedding_api_key == "sk-openai-key-value"
    assert settings.embedding_base_url == "https://api.openai.com/v1"
    assert settings.embedding_model == "text-embedding-3-large"
    assert settings.embedding_setup_problem() == ""


def test_incomplete_embedding_setup_is_explained_not_silently_accepted(qt_app):
    """Choosing a provider without a key must say so before a session starts."""
    dialog = _dialog(qt_app)

    dialog.embedding_provider_combo.setCurrentIndex(
        dialog.embedding_provider_combo.findData("gemini")
    )

    assert "ключ" in dialog.embedding_status_label.text().lower()
    assert dialog.qa_settings().embedding_setup_problem() != ""


def test_keys_are_never_shown_in_full(qt_app):
    """A dropdown of API keys must stay unreadable over someone's shoulder."""
    dialog = _dialog(
        qt_app, api_keys=[{"key": "AIzaSy-super-secret-key", "provider": "gemini"}]
    )

    labels = [
        dialog.embedding_key_combo.itemText(index)
        for index in range(dialog.embedding_key_combo.count())
    ]

    assert all("super-secret" not in label for label in labels)
    assert any("…" in label for label in labels)


def test_stage_switches_round_trip_through_the_dialog(qt_app):
    """The dialog is the only place to turn quality control off; it must work."""
    dialog = _dialog(
        qt_app,
        settings=QaSettings(
            check_completeness_after_chapter=False,
            auto_repair_confirmed_omissions=False,
            capabilities=QaCapabilitySettings(slovnet_enabled=True),
        ),
    )

    assert dialog.completeness_check.isChecked() is False
    assert dialog.repair_omissions_check.isChecked() is False
    settings = dialog.qa_settings()
    assert settings.check_completeness_after_chapter is False
    assert settings.capabilities.slovnet_enabled is True
    assert settings.capabilities.razdel_enabled is True


def test_settings_changes_are_published_once_edited(qt_app):
    """The page saves what the dialog reports; silence would lose the change."""
    dialog = _dialog(qt_app)
    published: list[QaSettings] = []
    dialog.settings_changed.connect(published.append)

    dialog.language_check.setChecked(False)

    assert published
    assert published[-1].check_language_after_chapter is False


def test_table_model_rejects_anything_but_a_snapshot(qt_app):
    """A live DataFrame from a background thread must never reach the table."""
    model = ChapterQaTableModel()

    with pytest.raises(TypeError):
        model.set_snapshot({"rows": []})


def test_progress_reports_real_counts(qt_app):
    """A whole-book pass must show how far it actually is."""
    dialog = _dialog(qt_app)

    dialog.set_progress(2, 6, "chapter-2")

    assert dialog.progress.maximum() == 6
    assert dialog.progress.value() == 2
    assert "chapter-2" in dialog.progress.format()


def test_export_is_offered_only_when_there_is_a_report(qt_app):
    """Exporting an empty report would hand the user four empty files."""
    dialog = TranslationQualityDialog()

    assert dialog.export_button.isEnabled() is False

    dialog.set_report(BookQaReportSnapshot.from_journal(_journal()))
    assert dialog.export_button.isEnabled() is True

    dialog.set_busy(True)
    assert dialog.export_button.isEnabled() is False


def test_the_chunk_spin_offers_the_automatic_size(qt_app):
    """Нижнее положение крутилки — «как при переводе», а не запрещённый ноль."""
    dialog = _dialog(qt_app)

    assert dialog.language_chunk_spin.minimum() == 0
    assert dialog.language_chunk_spin.specialValueText()
    dialog.language_chunk_spin.setValue(0)

    assert dialog.qa_settings().language_chunk_chars == 0
