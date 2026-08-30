"""Atomic structural insertion of one confirmed repair into a translated chapter."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from gemini_translator.qa.capabilities import QaCapabilitySettings
from gemini_translator.qa.models import GlossaryPolicy, RelevantGlossaryTerm
from gemini_translator.qa.repair_store import ManualEditConflict, RepairStore
from gemini_translator.qa.semantic_units import SemanticUnitExtractor
from gemini_translator.qa.structural_repair import (
    RepairLocationError,
    RepairValidationContext,
    StaleChapterError,
    StructuralPatch,
    StructuralRepairEngine,
    document_fingerprint,
)
from gemini_translator.utils.epub_json import (
    build_html_document_model,
    build_translation_payload,
)


_CHAPTER_HTML = (
    "<h1>Глава первая</h1>"
    "<p>Она посмотрела на ворота. Потом начался дождь.</p>"
    "<p>Башня стояла у самой реки.</p>"
)
_FRAGMENT = "Он так и не сказал ей, что башня уже пала."


def _model(html: str = _CHAPTER_HTML) -> dict:
    return build_html_document_model(html, document_id="chapter-1")


def _units(model: dict) -> tuple:
    extractor = SemanticUnitExtractor(QaCapabilitySettings())
    return extractor.extract(build_translation_payload(model), "ru")


def _engine() -> StructuralRepairEngine:
    return StructuralRepairEngine("ru", QaCapabilitySettings())


def _patch(model: dict, **overrides: object) -> StructuralPatch:
    units = _units(model)
    left = next(unit for unit in units if unit.text.startswith("Она посмотрела"))
    right = next(unit for unit in units if unit.text.startswith("Потом начался"))
    values: dict[str, object] = {
        "patch_id": "patch-0001",
        "chapter_id": "chapter-1",
        "expected_fingerprint": document_fingerprint(model),
        "left_anchor_unit_id": left.unit_id,
        "right_anchor_unit_id": right.unit_id,
        "parent_block_id": left.block_id,
        "translated_fragment": _FRAGMENT,
    }
    values.update(overrides)
    return StructuralPatch(**values)  # type: ignore[arg-type]


@pytest.fixture()
def chapter_file(tmp_path: Path) -> Path:
    path = tmp_path / "chapter-1.html"
    path.write_text(_CHAPTER_HTML, encoding="utf-8")
    return path


@pytest.fixture()
def store(tmp_path: Path) -> RepairStore:
    return RepairStore(tmp_path / "translation_qa_backups", session_id="session-1")


def _apply(engine: StructuralRepairEngine, chapter_file: Path, patch, store: RepairStore):
    model = build_html_document_model(
        chapter_file.read_text(encoding="utf-8"), document_id=patch.chapter_id
    )
    preview = engine.preview(model, patch)
    return engine.commit(preview, chapter_file, store)


def test_stale_fingerprint_never_modifies_the_chapter(chapter_file: Path, store):
    """Patching a chapter that changed under us would corrupt an unrelated edit."""
    model = _model()
    patch = _patch(model, expected_fingerprint="sha256:" + "0" * 64)
    before = chapter_file.read_bytes()

    with pytest.raises(StaleChapterError):
        _engine().preview(model, patch)

    assert chapter_file.read_bytes() == before


@pytest.mark.parametrize(
    "overrides",
    [
        {"left_anchor_unit_id": "u-" + "0" * 20},
        {"right_anchor_unit_id": "u-" + "0" * 20},
        {"parent_block_id": "unknown-block"},
    ],
)
def test_missing_or_mismatched_anchors_never_modify_the_chapter(
    chapter_file: Path, overrides
):
    """Without two verified anchors there is no safe place to insert anything."""
    model = _model()
    before = chapter_file.read_bytes()

    with pytest.raises(RepairLocationError):
        _engine().preview(model, _patch(model, **overrides))

    assert chapter_file.read_bytes() == before


def test_non_adjacent_anchors_are_refused():
    """A gap spanning other sentences is not a single insertion point."""
    model = _model()
    units = _units(model)
    far_right = next(unit for unit in units if unit.text.startswith("Башня стояла"))

    with pytest.raises(RepairLocationError):
        _engine().preview(
            model,
            _patch(
                model,
                right_anchor_unit_id=far_right.unit_id,
            ),
        )


def test_preview_inserts_between_anchors_and_preserves_every_other_node(chapter_file):
    """A repair must add content without renaming or rewriting anything else."""
    model = _model()
    preview = _engine().preview(model, _patch(model))

    assert preview.status == "prepared"
    assert _FRAGMENT in preview.rendered_html
    assert preview.rendered_html.index("Она посмотрела") < preview.rendered_html.index(
        _FRAGMENT
    )
    assert preview.rendered_html.index(_FRAGMENT) < preview.rendered_html.index(
        "Потом начался"
    )
    assert "Башня стояла у самой реки." in preview.rendered_html

    before_blocks = build_translation_payload(model)["blocks"]
    after_blocks = build_translation_payload(preview.document_model)["blocks"]
    assert [block["id"] for block in before_blocks] == [
        block["id"] for block in after_blocks
    ]
    assert chapter_file.read_text(encoding="utf-8") == _CHAPTER_HTML


def test_commit_is_atomic_and_idempotent(chapter_file: Path, store: RepairStore):
    """Re-running a finished repair must not duplicate the inserted sentence."""
    engine = _engine()
    model = _model()
    patch = _patch(model)

    first = _apply(engine, chapter_file, patch, store)
    assert first.status == "applied"
    assert chapter_file.read_text(encoding="utf-8").count(_FRAGMENT) == 1

    second_model = build_html_document_model(
        chapter_file.read_text(encoding="utf-8"), document_id="chapter-1"
    )
    second_preview = engine.preview(second_model, patch)
    second = engine.commit(second_preview, chapter_file, store)

    assert second.status == "already_applied"
    assert chapter_file.read_text(encoding="utf-8").count(_FRAGMENT) == 1


def test_commit_refuses_a_chapter_edited_after_the_preview(chapter_file, store):
    """A manual edit between preview and commit must win over an automatic repair."""
    engine = _engine()
    model = _model()
    preview = engine.preview(model, _patch(model))
    chapter_file.write_text(_CHAPTER_HTML + "<p>Ручная правка.</p>", encoding="utf-8")
    edited = chapter_file.read_bytes()

    with pytest.raises(ManualEditConflict):
        engine.commit(preview, chapter_file, store)

    assert chapter_file.read_bytes() == edited


def test_failed_bookkeeping_restores_the_original_chapter(chapter_file, store):
    """A half-applied repair with no journal entry would be invisible and unrepeatable."""
    engine = _engine()
    model = _model()
    before = chapter_file.read_bytes()

    class BrokenStore(RepairStore):
        def record_applied(self, applied):  # type: ignore[override]
            raise OSError("journal is not writable")

    broken = BrokenStore(store.root, session_id="session-1")
    preview = engine.preview(model, _patch(model))

    with pytest.raises(OSError):
        engine.commit(preview, chapter_file, broken)

    assert chapter_file.read_bytes() == before


def test_validation_collects_every_reason_without_short_circuiting():
    """Hiding later failures behind the first one would make repairs unauditable."""
    model = _model()
    engine = _engine()
    preview = engine.preview(
        model,
        _patch(model, translated_fragment="Он оставил the tower нетронутой."),
    )
    context = RepairValidationContext(
        source_text="He left the tower untouched.",
        glossary=(
            RelevantGlossaryTerm("tower", "башня", GlossaryPolicy.MUST_TRANSLATE, 1, 0),
        ),
    )

    validation = engine.validate(preview, context)

    assert validation.accepted is False
    assert "glossary_violation" in validation.reasons


def test_validation_accepts_a_clean_local_insertion():
    """The success path must stay reachable, or every repair silently degrades."""
    model = _model()
    engine = _engine()
    preview = engine.preview(model, _patch(model))

    validation = engine.validate(
        preview,
        RepairValidationContext(
            source_text="He never told her the tower had already fallen.",
            glossary=(
                RelevantGlossaryTerm(
                    "tower", "башня", GlossaryPolicy.MUST_TRANSLATE, 1, 0
                ),
            ),
        ),
    )

    assert validation.accepted is True
    assert validation.reasons == ()


def test_duplicate_insertion_text_is_rejected():
    """Inserting a sentence the chapter already contains would create a repetition."""
    model = _model()
    engine = _engine()
    preview = engine.preview(
        model, _patch(model, translated_fragment="Потом начался дождь.")
    )

    validation = engine.validate(preview, RepairValidationContext())

    assert validation.accepted is False
    assert "duplicate_fragment" in validation.reasons


def test_document_fingerprint_tracks_visible_text_and_identity():
    """A fingerprint blind to text edits would authorize a stale patch."""
    model = _model()
    other = _model(_CHAPTER_HTML.replace("дождь", "снег"))

    assert document_fingerprint(model) == document_fingerprint(_model())
    assert document_fingerprint(model) != document_fingerprint(other)
    assert document_fingerprint(model).startswith("sha256:")
    assert (
        document_fingerprint(model).split(":", 1)[1]
        != hashlib.sha256(b"").hexdigest()
    )
