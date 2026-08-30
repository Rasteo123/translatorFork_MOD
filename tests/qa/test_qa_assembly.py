"""Building QA from real project state must degrade, never crash a session."""

from __future__ import annotations

import json
from pathlib import Path
import zipfile

import pytest

from gemini_translator.core.chapter_qa_coordinator import TranslationReadyEvent
from gemini_translator.qa.assembly import (
    QaAssemblyError,
    build_chapter_qa_request,
    build_embedding_provider,
    load_project_glossary_terms,
)
from gemini_translator.qa.embeddings.factory import EmbeddingUnavailableError
from gemini_translator.qa.llm import QaModelSelection
from gemini_translator.qa.models import GlossaryPolicy
from gemini_translator.qa.settings import QaSettings


_SOURCE_HTML = "<p>He opened the door. The room was empty.</p>"
_TARGET_HTML = "<p>Он открыл дверь. Комната была пуста.</p>"


class _ProjectManager:
    def __init__(self, folder: Path) -> None:
        self.project_folder = str(folder)

    def get_translation_qa_journal_path(self) -> Path:
        return Path(self.project_folder) / "translation_qa.json"

    def get_translation_qa_backup_dir(self) -> Path:
        return Path(self.project_folder) / "translation_qa_backups"

    def get_translation_qa_embedding_cache_dir(self) -> Path:
        return Path(self.project_folder) / "translation_qa_embedding_cache"


@pytest.fixture()
def project(tmp_path: Path) -> _ProjectManager:
    (tmp_path / "project_glossary.json").write_text(
        json.dumps(
            [
                {"original": "tower", "rus": "башня"},
                {"original": "Nokia", "rus": "Nokia"},
                {"original": "", "rus": "пусто"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return _ProjectManager(tmp_path)


def _epub(tmp_path: Path, chapter_path: str = "OEBPS/chapter-1.xhtml") -> Path:
    path = tmp_path / "book.epub"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(chapter_path, _SOURCE_HTML)
    return path


def _event(tmp_path: Path, **overrides) -> TranslationReadyEvent:
    translated = tmp_path / "chapter-1.html"
    translated.write_text(_TARGET_HTML, encoding="utf-8")
    values = {
        "task_id": "task-1",
        "chapter_id": "OEBPS/chapter-1.xhtml",
        "source_path": "OEBPS/chapter-1.xhtml",
        "translated_path": str(translated),
        "source_language": "auto",
        "target_language": "ru",
        "epub_path": str(_epub(tmp_path)),
    }
    values.update(overrides)
    return TranslationReadyEvent(**values)


def test_request_is_built_from_the_epub_and_the_saved_translation(tmp_path, project):
    """The checker must compare the real source with the real saved chapter."""
    request = build_chapter_qa_request(
        _event(tmp_path),
        project_manager=project,
        qa_settings=QaSettings(),
        model=QaModelSelection("gemini", "qa-model"),
        session_id="session-1",
        source_language_resolver=lambda html: "en",
    )

    assert request is not None
    assert request.chapter_id == "OEBPS/chapter-1.xhtml"
    assert request.source_language == "en"
    assert request.target_language == "ru"
    source_text = "".join(
        fragment["text"]
        for block in request.coverage_request.source_payload["blocks"]
        for fragment in block["inlines"]
        if fragment.get("type") == "text"
    )
    assert "He opened the door." in source_text
    assert request.target_document_id == "OEBPS/chapter-1.xhtml"


def test_project_glossary_reaches_the_request(tmp_path, project):
    """A repair must see the book's own terms, in their own policies."""
    request = build_chapter_qa_request(
        _event(tmp_path),
        project_manager=project,
        qa_settings=QaSettings(),
        model=QaModelSelection("gemini", "qa-model"),
        session_id="session-1",
    )

    assert request is not None
    policies = {term.original: term.policy for term in request.glossary}
    assert policies["tower"] is GlossaryPolicy.MUST_TRANSLATE
    assert policies["Nokia"] is GlossaryPolicy.KEEP_ORIGINAL
    assert "" not in policies


@pytest.mark.parametrize(
    "overrides",
    [
        {"epub_path": "/nonexistent/book.epub"},
        {"source_path": "OEBPS/missing.xhtml"},
        {"translated_path": "/nonexistent/chapter.html"},
    ],
)
def test_unreadable_state_skips_the_chapter_instead_of_raising(
    tmp_path, project, overrides
):
    """Missing files during a live run are ordinary, not a reason to crash."""
    assert (
        build_chapter_qa_request(
            _event(tmp_path, **overrides),
            project_manager=project,
            qa_settings=QaSettings(),
            model=QaModelSelection("gemini", "qa-model"),
            session_id="session-1",
        )
        is None
    )


def test_empty_chapters_are_skipped(tmp_path, project):
    """An empty chapter has nothing to compare and must cost no request."""
    empty = tmp_path / "empty.html"
    empty.write_text("   ", encoding="utf-8")

    assert (
        build_chapter_qa_request(
            _event(tmp_path, translated_path=str(empty)),
            project_manager=project,
            qa_settings=QaSettings(),
            model=QaModelSelection("gemini", "qa-model"),
            session_id="session-1",
        )
        is None
    )


def test_glossary_is_ignored_when_the_file_is_broken(tmp_path):
    """A hand-edited glossary must not stop quality control."""
    (tmp_path / "project_glossary.json").write_text("{ broken", encoding="utf-8")

    assert load_project_glossary_terms(tmp_path) == ()
    assert load_project_glossary_terms(tmp_path / "missing") == ()


def test_embedding_provider_needs_a_configured_key(tmp_path):
    """Semantic checking must be refused clearly when no provider is set up."""
    with pytest.raises(EmbeddingUnavailableError):
        build_embedding_provider(QaSettings(), lambda: object(), {})

    provider = build_embedding_provider(
        QaSettings(embedding_provider="gemini"),
        lambda: object(),
        {"google": "key"},
        tmp_path / "cache",
    )
    assert provider is not None


def test_a_corrupted_journal_refuses_to_be_overwritten(tmp_path, project):
    """A damaged decision history is shown to the user, never silently replaced."""
    from gemini_translator.qa.assembly import build_translation_quality_service

    (Path(project.project_folder) / "translation_qa.json").write_text(
        "not json at all", encoding="utf-8"
    )

    with pytest.raises(QaAssemblyError):
        build_translation_quality_service(
            project_manager=project,
            qa_settings=QaSettings(),
            handler_factory=lambda model: object(),
            embedding_provider=object(),
            session_id="session-1",
        )
