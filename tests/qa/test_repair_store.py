"""Reversible, hash-verified backups for every automatically repaired chapter."""

from __future__ import annotations

from pathlib import Path

import pytest

from gemini_translator.qa.capabilities import QaCapabilitySettings
from gemini_translator.qa.repair_store import RepairStore, RepairStoreError
from gemini_translator.qa.semantic_units import SemanticUnitExtractor
from gemini_translator.qa.structural_repair import (
    StructuralPatch,
    StructuralRepairEngine,
    document_fingerprint,
)
from gemini_translator.utils.epub_json import (
    build_html_document_model,
    build_translation_payload,
)


_CHAPTER_HTML = (
    "<p>Она посмотрела на ворота. Потом начался дождь.</p>"
    "<p>Башня стояла у самой реки.</p>"
)
_FRAGMENT = "Он так и не сказал ей правду."


def _engine() -> StructuralRepairEngine:
    return StructuralRepairEngine("ru", QaCapabilitySettings())


def _apply(chapter_file: Path, store: RepairStore, chapter_id: str, patch_id: str):
    model = build_html_document_model(
        chapter_file.read_text(encoding="utf-8"), document_id=chapter_id
    )
    units = SemanticUnitExtractor(QaCapabilitySettings()).extract(
        build_translation_payload(model), "ru"
    )
    left = next(unit for unit in units if unit.text.startswith("Она посмотрела"))
    right = next(unit for unit in units if unit.text.startswith("Потом начался"))
    patch = StructuralPatch(
        patch_id=patch_id,
        chapter_id=chapter_id,
        expected_fingerprint=document_fingerprint(model),
        left_anchor_unit_id=left.unit_id,
        right_anchor_unit_id=right.unit_id,
        parent_block_id=left.block_id,
        translated_fragment=_FRAGMENT,
    )
    engine = _engine()
    return engine.commit(engine.preview(model, patch), chapter_file, store)


@pytest.fixture()
def chapter_file(tmp_path: Path) -> Path:
    path = tmp_path / "chapter-1.html"
    path.write_text(_CHAPTER_HTML, encoding="utf-8")
    return path


@pytest.fixture()
def store(tmp_path: Path) -> RepairStore:
    return RepairStore(tmp_path / "backups", session_id="session-1")


def test_undo_restores_the_chapter_byte_for_byte(chapter_file: Path, store: RepairStore):
    """Any automatic edit must be fully reversible without calling a model again."""
    original = chapter_file.read_bytes()
    applied = _apply(chapter_file, store, "chapter-1", "patch-1")

    assert applied.status == "applied"
    assert chapter_file.read_bytes() != original

    result = store.undo_chapter("chapter-1")

    assert result.status == "restored"
    assert result.patches == ("patch-1",)
    assert chapter_file.read_bytes() == original


def test_second_undo_reports_nothing_to_undo(chapter_file: Path, store: RepairStore):
    """A repeated undo must be a no-op, not a second destructive restore."""
    _apply(chapter_file, store, "chapter-1", "patch-1")
    store.undo_chapter("chapter-1")
    restored = chapter_file.read_bytes()

    result = store.undo_chapter("chapter-1")

    assert result.status == "nothing_to_undo"
    assert result.patches == ()
    assert chapter_file.read_bytes() == restored


def test_undo_session_reverses_every_chapter_it_touched(tmp_path: Path, store):
    """Undoing a session must leave no partially repaired chapter behind."""
    originals = {}
    for index in (1, 2):
        path = tmp_path / f"chapter-{index}.html"
        path.write_text(_CHAPTER_HTML, encoding="utf-8")
        originals[path] = path.read_bytes()
        _apply(path, store, f"chapter-{index}", f"patch-{index}")

    result = store.undo_session("session-1")

    assert result.status == "restored"
    assert set(result.chapters) == {"chapter-1", "chapter-2"}
    assert all(path.read_bytes() == data for path, data in originals.items())


def test_manual_edit_after_a_repair_blocks_undo(chapter_file: Path, store: RepairStore):
    """Undo must never silently discard a human edit made after the repair."""
    _apply(chapter_file, store, "chapter-1", "patch-1")
    chapter_file.write_text(
        chapter_file.read_text(encoding="utf-8") + "<p>Ручная правка.</p>",
        encoding="utf-8",
    )
    edited = chapter_file.read_bytes()

    result = store.undo_chapter("chapter-1")

    assert result.status == "manual_edit_conflict"
    assert chapter_file.read_bytes() == edited


def test_corrupted_backup_is_never_restored(chapter_file: Path, store: RepairStore):
    """Restoring an unverified backup could overwrite a chapter with garbage."""
    applied = _apply(chapter_file, store, "chapter-1", "patch-1")
    Path(applied.backup_path).write_text("повреждено", encoding="utf-8")
    repaired = chapter_file.read_bytes()

    with pytest.raises(RepairStoreError):
        store.undo_chapter("chapter-1")

    assert chapter_file.read_bytes() == repaired


def test_backup_is_written_once_per_chapter_and_session(chapter_file, store):
    """A second backup would capture already-repaired content as the original."""
    original = chapter_file.read_bytes()
    first = store.backup_chapter("chapter-1", chapter_file)
    chapter_file.write_text("<p>изменено</p>", encoding="utf-8")
    second = store.backup_chapter("chapter-1", chapter_file)

    assert second.path == first.path
    assert Path(first.path).read_bytes() == original


def test_store_rejects_unusable_identity(tmp_path: Path):
    """Empty identities would collide across chapters and sessions on disk."""
    with pytest.raises(RepairStoreError):
        RepairStore(tmp_path / "backups", session_id="  ")
    store = RepairStore(tmp_path / "backups", session_id="session-1")
    with pytest.raises(RepairStoreError):
        store.backup_chapter("", tmp_path / "missing.html")
    with pytest.raises(RepairStoreError):
        store.backup_chapter("chapter-1", tmp_path / "missing.html")
