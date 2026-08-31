"""Structural, reversible insertion of one confirmed repair into a chapter."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re
import unicodedata

from ..utils.epub_json import (
    build_html_document_model,
    build_translation_payload,
    insert_block_after,
    insert_text_between_units,
    render_document_html,
)
from ..utils.text import validate_html_structure
from .capabilities import QaCapabilitySettings
from .glossary_audit import match_glossary_policies
from .models import (
    GlossaryPolicy,
    GlossaryRule,
    QaModelValidationError,
    RelevantGlossaryTerm,
    SemanticUnit,
)
from .repair_store import (
    AppliedRepair,
    ManualEditConflict,
    RepairStore,
    atomic_write_bytes,
    content_digest,
)
from .semantic_units import SemanticUnitExtractor, flatten_visible_text


REPAIR_MARKER_ATTRIBUTE = "data-qa-repair"


class StructuralRepairError(RuntimeError):
    """Base class for refusals raised before any chapter byte is written."""


class StaleChapterError(StructuralRepairError):
    """Raised when the chapter no longer matches the fingerprint of the patch."""


class RepairLocationError(StructuralRepairError):
    """Raised when the patch does not describe one unambiguous insertion point."""


@dataclass(frozen=True, slots=True)
class StructuralPatch:
    """One fully identified insertion of a translated fragment into a chapter."""

    patch_id: str
    chapter_id: str
    expected_fingerprint: str
    left_anchor_unit_id: str
    right_anchor_unit_id: str
    parent_block_id: str
    translated_fragment: str

    def __post_init__(self) -> None:
        for field_name in (
            "patch_id",
            "chapter_id",
            "expected_fingerprint",
            "left_anchor_unit_id",
            "right_anchor_unit_id",
            "parent_block_id",
            "translated_fragment",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise QaModelValidationError(f"{field_name} must be a nonempty string")
        if self.left_anchor_unit_id == self.right_anchor_unit_id:
            raise QaModelValidationError("a patch needs two distinct anchors")


@dataclass(frozen=True, slots=True, eq=False)
class RepairPreview:
    """An in-memory candidate chapter that has touched no file yet."""

    patch: StructuralPatch
    document_model: dict
    rendered_html: str
    before_html: str
    before_fingerprint: str
    after_fingerprint: str
    inserted_text: str
    block_id: str
    status: str = "prepared"
    # "inline" puts the fragment inside the left anchor's block; "block" gives a
    # lost paragraph a paragraph of its own, after the block it follows.
    placement: str = "inline"


@dataclass(frozen=True, slots=True)
class RepairValidationContext:
    """Extra evidence a preview is checked against before it may be committed."""

    source_text: str = ""
    glossary: tuple[RelevantGlossaryTerm, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class RepairValidation:
    """Complete verdict listing every reason a preview was refused."""

    accepted: bool
    reasons: tuple[str, ...] = ()


def document_fingerprint(document_model: dict) -> str:
    """Return a stable fingerprint over block identity and visible text."""
    payload = build_translation_payload(document_model)
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class StructuralRepairEngine:
    """Insert one fragment between two verified anchors, atomically or not at all.

    Every refusal happens before a file is opened for writing: a stale
    fingerprint, a missing anchor, or a chapter edited after the preview leaves
    the chapter exactly as it was.
    """

    def __init__(
        self, language: str, capabilities: QaCapabilitySettings | None = None
    ) -> None:
        if not isinstance(language, str) or not language.strip():
            raise QaModelValidationError("language must be a nonempty string")
        self._language = language.strip()
        self._extractor = SemanticUnitExtractor(capabilities or QaCapabilitySettings())

    def preview(self, document_model: dict, patch: StructuralPatch) -> RepairPreview:
        """Build the candidate chapter in memory, or refuse with a typed error."""
        if not isinstance(document_model, dict):
            raise QaModelValidationError("document_model must be a dict")
        if not isinstance(patch, StructuralPatch):
            raise QaModelValidationError("patch must be a StructuralPatch")

        before_html = render_document_html(document_model)
        fingerprint = document_fingerprint(document_model)
        if _contains_repair_marker(document_model, patch.patch_id):
            return RepairPreview(
                patch=patch,
                document_model=document_model,
                rendered_html=before_html,
                before_html=before_html,
                before_fingerprint=fingerprint,
                after_fingerprint=fingerprint,
                inserted_text="",
                block_id=patch.parent_block_id,
                status="already_applied",
            )
        if fingerprint != patch.expected_fingerprint:
            raise StaleChapterError(
                "chapter fingerprint does not match the patch expectation"
            )

        payload = build_translation_payload(document_model)
        units = self._extractor.extract(payload, self._language)
        left = _unit_by_id(units, patch.left_anchor_unit_id)
        right = _unit_by_id(units, patch.right_anchor_unit_id)
        if left is None or right is None:
            raise RepairLocationError("both anchors must exist in the chapter")
        if left.ordinal + 1 != right.ordinal:
            raise RepairLocationError("anchors must be adjacent semantic units")
        if left.block_id != patch.parent_block_id:
            raise RepairLocationError("parent block must own the left anchor")

        if left.block_id != right.block_id:
            return self._preview_new_block(
                document_model, patch, before_html, fingerprint
            )

        block = next(
            (item for item in payload["blocks"] if item["id"] == patch.parent_block_id),
            None,
        )
        if block is None:
            raise RepairLocationError("parent block is not part of the chapter")
        block_text, segments = flatten_visible_text(block["inlines"])
        anchor_span = left.inline_spans[-1]
        segment = next(
            (item for item in segments if item[0] == anchor_span.inline_id), None
        )
        if segment is None:
            raise RepairLocationError("anchor inline node is missing from the block")

        inserted_text = _separated_fragment(
            block_text, anchor_span.source_end, patch.translated_fragment
        )
        model = insert_text_between_units(
            document_model,
            {
                "parent_block_id": patch.parent_block_id,
                "anchor_node_id": anchor_span.inline_id,
                "offset": anchor_span.source_end - segment[1],
                "node_id": f"qa-repair.{patch.patch_id}",
                "tail_node_id": f"qa-repair.{patch.patch_id}.tail",
                "wrapper_tag": "span",
                "wrapper_attrs": [
                    {"name": REPAIR_MARKER_ATTRIBUTE, "value": patch.patch_id}
                ],
            },
            inserted_text,
        )
        return RepairPreview(
            patch=patch,
            document_model=model,
            rendered_html=render_document_html(model),
            before_html=before_html,
            before_fingerprint=fingerprint,
            after_fingerprint=document_fingerprint(model),
            inserted_text=inserted_text,
            block_id=patch.parent_block_id,
        )

    def _preview_new_block(
        self,
        document_model: dict,
        patch: StructuralPatch,
        before_html: str,
        fingerprint: str,
    ) -> RepairPreview:
        """Restore a whole lost paragraph as its own block after the left anchor."""
        try:
            model = insert_block_after(
                document_model,
                {
                    "after_block_id": patch.parent_block_id,
                    "node_id": f"qa-repair.{patch.patch_id}",
                    "text_node_id": f"qa-repair.{patch.patch_id}.text",
                    "attrs": [
                        {"name": REPAIR_MARKER_ATTRIBUTE, "value": patch.patch_id}
                    ],
                },
                patch.translated_fragment,
            )
        except ValueError as error:
            raise RepairLocationError(str(error)) from error
        return RepairPreview(
            patch=patch,
            document_model=model,
            rendered_html=render_document_html(model),
            before_html=before_html,
            before_fingerprint=fingerprint,
            after_fingerprint=document_fingerprint(model),
            inserted_text=patch.translated_fragment,
            block_id=f"qa-repair.{patch.patch_id}",
            placement="block",
        )

    def validate(
        self, preview: RepairPreview, context: RepairValidationContext
    ) -> RepairValidation:
        """Collect every reason the preview is unsafe, without short-circuiting."""
        if not isinstance(preview, RepairPreview):
            raise QaModelValidationError("preview must be a RepairPreview")
        if not isinstance(context, RepairValidationContext):
            raise QaModelValidationError("context must be a RepairValidationContext")
        if preview.status != "prepared":
            return RepairValidation(False, ("already_applied",))

        patch = preview.patch
        reasons: list[str] = []
        before_payload = build_translation_payload(
            build_html_document_model(preview.before_html, document_id=patch.chapter_id)
        )
        after_payload = build_translation_payload(preview.document_model)

        if not _block_identity_kept(before_payload, after_payload, preview):
            reasons.append("block_identity_changed")
        elif not _only_patched_block_changed(
            before_payload, after_payload, preview
        ):
            reasons.append("unrelated_text_changed")

        if not _anchors_surround_fragment(after_payload, preview):
            reasons.append("anchor_order_broken")

        if _visible_occurrences(after_payload, patch.translated_fragment) != 1:
            reasons.append("duplicate_fragment")

        try:
            is_valid, _reason, _html = validate_html_structure(
                preview.before_html, preview.rendered_html
            )
        except Exception:  # noqa: BLE001 - a validator crash must not authorize a write
            is_valid = False
        if not is_valid:
            reasons.append("invalid_html")

        if _violates_glossary(
            patch.translated_fragment, context.source_text, context.glossary
        ):
            reasons.append("glossary_violation")

        return RepairValidation(not reasons, tuple(reasons))

    def commit(
        self, preview: RepairPreview, chapter_path: Path | str, store: RepairStore
    ) -> AppliedRepair:
        """Write the previewed chapter atomically, or leave the file untouched."""
        if not isinstance(preview, RepairPreview):
            raise QaModelValidationError("preview must be a RepairPreview")
        if not isinstance(store, RepairStore):
            raise QaModelValidationError("store must be a RepairStore")
        patch = preview.patch
        path = Path(chapter_path)

        if preview.status == "already_applied":
            return AppliedRepair(
                patch_id=patch.patch_id,
                chapter_id=patch.chapter_id,
                session_id=store.session_id,
                chapter_path=path,
                backup_path=store.root / patch.chapter_id,
                before_sha256="",
                after_sha256="",
                inserted_text="",
                status="already_applied",
            )

        current_bytes = path.read_bytes()
        current_model = build_html_document_model(
            current_bytes.decode("utf-8"),
            document_id=str(preview.document_model.get("document_id") or patch.chapter_id),
        )
        if document_fingerprint(current_model) != preview.before_fingerprint:
            raise ManualEditConflict(
                "chapter changed between preview and commit; repair not applied"
            )

        backup = store.backup_chapter(patch.chapter_id, path)
        payload = preview.rendered_html.encode("utf-8")
        atomic_write_bytes(path, payload)
        applied = AppliedRepair(
            patch_id=patch.patch_id,
            chapter_id=patch.chapter_id,
            session_id=store.session_id,
            chapter_path=path,
            backup_path=backup.path,
            before_sha256=content_digest(current_bytes),
            after_sha256=content_digest(payload),
            inserted_text=preview.inserted_text,
        )
        try:
            store.record_applied(applied)
        except Exception:
            atomic_write_bytes(path, current_bytes)
            raise
        return applied


def _unit_by_id(
    units: tuple[SemanticUnit, ...], unit_id: str
) -> SemanticUnit | None:
    return next((unit for unit in units if unit.unit_id == unit_id), None)


def _separated_fragment(block_text: str, offset: int, fragment: str) -> str:
    """Add only the spacing the surrounding text does not already provide."""
    previous = block_text[offset - 1] if offset > 0 else ""
    following = block_text[offset] if offset < len(block_text) else ""
    prefix = "" if not previous or previous.isspace() else " "
    suffix = "" if not following or following.isspace() else " "
    return f"{prefix}{fragment}{suffix}"


def _contains_repair_marker(document_model: dict, patch_id: str) -> bool:
    def walk(node: dict) -> bool:
        for item in node.get("attrs", []) or []:
            if (
                str(item.get("name", "")).lower() == REPAIR_MARKER_ATTRIBUTE
                and str(item.get("value", "")) == patch_id
            ):
                return True
        return any(walk(child) for child in node.get("children", []) or [])

    return walk(document_model.get("body", {}))


def _block_text(payload: dict, block_id: str) -> str:
    block = next(
        (item for item in payload["blocks"] if item["id"] == block_id), None
    )
    if block is None:
        return ""
    return flatten_visible_text(block["inlines"])[0]


def _block_ids(payload: dict) -> list[str]:
    return [block["id"] for block in payload["blocks"]]


def _block_identity_kept(
    before_payload: dict, after_payload: dict, preview: RepairPreview
) -> bool:
    """No block may lose its identity; a new paragraph may only be added."""
    before_ids = _block_ids(before_payload)
    after_ids = _block_ids(after_payload)
    if preview.placement != "block":
        return before_ids == after_ids
    if len(after_ids) != len(before_ids) + 1:
        return False
    if preview.block_id not in after_ids:
        return False
    return [item for item in after_ids if item != preview.block_id] == before_ids


def _only_patched_block_changed(
    before_payload: dict, after_payload: dict, preview: RepairPreview
) -> bool:
    """Confirm the insertion is the single difference between both payloads."""
    inserted_text = preview.inserted_text
    after_blocks = [
        block
        for block in after_payload["blocks"]
        if not (preview.placement == "block" and block["id"] == preview.block_id)
    ]
    if preview.placement == "block":
        new_block = next(
            (
                block
                for block in after_payload["blocks"]
                if block["id"] == preview.block_id
            ),
            None,
        )
        if new_block is None:
            return False
        if flatten_visible_text(new_block["inlines"])[0] != inserted_text:
            return False
    for before_block, after_block in zip(
        before_payload["blocks"], after_blocks, strict=True
    ):
        before_text = flatten_visible_text(before_block["inlines"])[0]
        after_text = flatten_visible_text(after_block["inlines"])[0]
        if preview.placement == "inline" and before_block["id"] == preview.block_id:
            if len(after_text) != len(before_text) + len(inserted_text):
                return False
            if inserted_text not in after_text:
                return False
            if after_text.replace(inserted_text, "", 1) != before_text:
                return False
        elif before_text != after_text:
            return False
    return True


def _anchors_surround_fragment(after_payload: dict, preview: RepairPreview) -> bool:
    fragment = preview.patch.translated_fragment
    if preview.placement == "block":
        ids = _block_ids(after_payload)
        if preview.block_id not in ids or preview.patch.parent_block_id not in ids:
            return False
        if ids.index(preview.block_id) != ids.index(preview.patch.parent_block_id) + 1:
            return False
        return _block_text(after_payload, preview.block_id) == fragment
    text = _block_text(after_payload, preview.block_id)
    position = text.find(fragment)
    return position > 0 and position + len(fragment) <= len(text)


def _visible_occurrences(payload: dict, fragment: str) -> int:
    normalized_fragment = _normalize(fragment)
    if not normalized_fragment:
        return 0
    total = 0
    for block in payload["blocks"]:
        total += _normalize(flatten_visible_text(block["inlines"])[0]).count(
            normalized_fragment
        )
    return total


def _violates_glossary(
    fragment: str, source_text: str, glossary: tuple[RelevantGlossaryTerm, ...]
) -> bool:
    relevant = tuple(
        term
        for term in glossary
        if term.policy in {GlossaryPolicy.MUST_TRANSLATE, GlossaryPolicy.KEEP_ORIGINAL}
    )
    if not relevant or not source_text.strip():
        return False
    rules = tuple(GlossaryRule(term.original_term, term.policy) for term in relevant)
    in_source = {match.term for match in match_glossary_policies(source_text, rules)}
    in_fragment = {match.term for match in match_glossary_policies(fragment, rules)}
    for term in relevant:
        if term.original_term not in in_source:
            continue
        present = term.original_term in in_fragment
        if term.policy is GlossaryPolicy.MUST_TRANSLATE and present:
            return True
        if term.policy is GlossaryPolicy.KEEP_ORIGINAL and not present:
            return True
    return False


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip().casefold()
