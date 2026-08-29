"""Qt-free extraction of stable semantic units from EPUB translation payloads.

Only visible ``text`` fragments contribute characters.  The extractor never
rewrites the payload: offsets are half-open indexes into each block's flattened
visible text, so a caller can recover every unit with ``text[start:end]``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import re
import unicodedata

from razdel import sentenize

from .capabilities import QaCapabilitySettings
from .models import SemanticInlineSpan, SemanticUnit, SemanticWindow


class SemanticUnitExtractionError(ValueError):
    """Raised when an EPUB translation payload lacks stable extractable identity."""


class LegacyRussianSegmenter:
    """Small deterministic fallback used when Razdel is deliberately disabled.

    It recognizes common Russian abbreviations and initials, while still serving
    as the explicit conservative fallback for other alphabetic languages.
    """

    VERSION = "legacy-russian-v1"
    _ABBREVIATIONS = frozenset(
        {
            "акад.",
            "г.",
            "гг.",
            "гл.",
            "д.",
            "доц.",
            "им.",
            "кв.",
            "км.",
            "лит.",
            "млн.",
            "млрд.",
            "наб.",
            "проф.",
            "рис.",
            "стр.",
            "т.",
            "тов.",
            "ул.",
        }
    )
    _TRAILING_CLOSERS = frozenset('"”’»)]}』」】》')

    def spans(self, text: str) -> tuple[tuple[int, int], ...]:
        spans: list[tuple[int, int]] = []
        start = 0
        index = 0
        length = len(text)
        while index < length:
            character = text[index]
            if character not in ".!?…":
                index += 1
                continue
            if character == "." and self._is_abbreviation_or_initial(text, index):
                index += 1
                continue

            stop = index + 1
            while stop < length and text[stop] in ".!?…":
                stop += 1
            while stop < length and text[stop] in self._TRAILING_CLOSERS:
                stop += 1
            spans.append((start, stop))
            start = stop
            index = stop

        if start < length:
            spans.append((start, length))
        return tuple(spans)

    @classmethod
    def _is_abbreviation_or_initial(cls, text: str, period_index: int) -> bool:
        before = text[: period_index + 1]
        word_match = re.search(r"([^\s]+)$", before)
        token = word_match.group(1).casefold() if word_match else ""
        if token in cls._ABBREVIATIONS:
            return True

        initial_match = re.search(r"(?:^|\s)([A-Za-zА-ЯЁа-яё])\.$", before)
        if not initial_match:
            return False
        following = text[period_index + 1 :]
        return bool(re.match(r"\s*[A-Za-zА-ЯЁа-яё]\.", following))


class SemanticUnitExtractor:
    """Extract stable sentence spans from the ``build_translation_payload`` contract."""

    PREPROCESSING_VERSION = "semantic-units-v1"
    CJK_SEGMENTER_ID = "cjk-punctuation-v1"
    RAZDEL_SEGMENTER_ID = "razdel-0.5"
    LEGACY_ALPHABETIC_SEGMENTER_ID = "legacy-alphabetic-v1"

    def __init__(self, capabilities: QaCapabilitySettings):
        if not isinstance(capabilities, QaCapabilitySettings):
            raise TypeError("capabilities must be QaCapabilitySettings")
        self._capabilities = capabilities
        self._legacy_russian = LegacyRussianSegmenter()

    @property
    def preprocessing_identity(self) -> str:
        """Stable cache namespace that changes with the Razdel selection."""
        russian_segmenter = (
            self.RAZDEL_SEGMENTER_ID
            if self._capabilities.razdel_enabled
            else LegacyRussianSegmenter.VERSION
        )
        return "|".join(
            (
                self.PREPROCESSING_VERSION,
                self.CJK_SEGMENTER_ID,
                russian_segmenter,
                self.LEGACY_ALPHABETIC_SEGMENTER_ID,
            )
        )

    def cache_identity(self, language: str) -> str:
        """Return a language-specific stable identity for downstream embedding caches."""
        return "|".join(
            (self.PREPROCESSING_VERSION, self._segmenter_id(self._language_base(language)))
        )

    def extract(self, payload: dict, language: str) -> tuple[SemanticUnit, ...]:
        """Extract units without modifying any payload list, mapping, or text value."""
        document_id, blocks = self._validate_payload(payload)
        language_base = self._language_base(language)
        segmenter_id = self._segmenter_id(language_base)
        units: list[SemanticUnit] = []
        ordinal = 0

        for block in blocks:
            block_id = block["id"]
            visible_text, visible_segments = self._flatten_visible_text(block["inlines"])
            for start, end in self._segment_spans(visible_text, language_base):
                trimmed = self._trim_span(visible_text, start, end)
                if trimmed is None:
                    continue
                source_start, source_end = trimmed
                text = visible_text[source_start:source_end]
                normalized_text = self._normalize_text(text)
                identity = "\x1f".join(
                    (
                        self.PREPROCESSING_VERSION,
                        segmenter_id,
                        document_id,
                        block_id,
                        str(source_start),
                        str(source_end),
                        normalized_text,
                    )
                )
                units.append(
                    SemanticUnit(
                        unit_id="u-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20],
                        document_id=document_id,
                        block_id=block_id,
                        ordinal=ordinal,
                        text=text,
                        normalized_text=normalized_text,
                        source_start=source_start,
                        source_end=source_end,
                        kind=self._block_kind(block),
                        inline_spans=self._inline_spans_for_unit(
                            visible_segments, source_start, source_end
                        ),
                    )
                )
                ordinal += 1
        return tuple(units)

    def windows(
        self, units: Sequence[SemanticUnit], max_size: int = 3
    ) -> tuple[SemanticWindow, ...]:
        """Create all contiguous window sizes, never joining units across documents."""
        if isinstance(max_size, bool) or not isinstance(max_size, int) or max_size < 1:
            raise ValueError("max_size must be an integer greater than zero")

        grouped: dict[str, list[SemanticUnit]] = {}
        seen_unit_ids: set[str] = set()
        seen_ordinals_by_document: dict[str, set[int]] = {}
        for unit in units:
            if not isinstance(unit, SemanticUnit):
                raise ValueError("units must contain SemanticUnit values")
            unit.validate()
            if unit.unit_id in seen_unit_ids:
                raise ValueError("duplicate unit_id")
            seen_unit_ids.add(unit.unit_id)
            document_ordinals = seen_ordinals_by_document.setdefault(
                unit.document_id, set()
            )
            if unit.ordinal in document_ordinals:
                raise ValueError("duplicate ordinal within document")
            document_ordinals.add(unit.ordinal)
            grouped.setdefault(unit.document_id, []).append(unit)

        windows: list[SemanticWindow] = []
        for document_units in grouped.values():
            ordered_units = sorted(document_units, key=lambda unit: unit.ordinal)
            for start in range(len(ordered_units)):
                for size in range(1, min(max_size, len(ordered_units) - start) + 1):
                    window_units = ordered_units[start : start + size]
                    windows.append(
                        SemanticWindow(
                            unit_ids=tuple(unit.unit_id for unit in window_units),
                            text=" ".join(unit.text for unit in window_units),
                        )
                    )
        return tuple(windows)

    @classmethod
    def _validate_payload(cls, payload: object) -> tuple[str, list[Mapping[str, object]]]:
        if not isinstance(payload, Mapping):
            raise SemanticUnitExtractionError("payload must be a mapping")
        document_id = payload.get("document_id")
        if not isinstance(document_id, str) or not document_id.strip():
            raise SemanticUnitExtractionError("payload document_id must be a nonempty string")
        blocks = payload.get("blocks")
        if not isinstance(blocks, list):
            raise SemanticUnitExtractionError("payload blocks must be a list")

        validated_blocks: list[Mapping[str, object]] = []
        block_ids: set[str] = set()
        seen_fragment_ids: set[str] = set()
        for block in blocks:
            if not isinstance(block, Mapping):
                raise SemanticUnitExtractionError("payload block must be a mapping")
            block_id = block.get("id")
            if not isinstance(block_id, str) or not block_id.strip():
                raise SemanticUnitExtractionError("payload block id must be a nonempty string")
            if block_id in block_ids:
                raise SemanticUnitExtractionError("payload block ids must be unique")
            block_ids.add(block_id)
            inlines = block.get("inlines")
            if not isinstance(inlines, list):
                raise SemanticUnitExtractionError("payload block inlines must be a list")
            cls._validate_fragments(inlines, seen_fragment_ids)
            validated_blocks.append(block)
        return document_id, validated_blocks

    @classmethod
    def _validate_fragments(
        cls, fragments: list[object], seen_fragment_ids: set[str]
    ) -> None:
        for fragment in fragments:
            if not isinstance(fragment, Mapping):
                raise SemanticUnitExtractionError("inline fragment must be a mapping")
            fragment_id = fragment.get("id")
            if not isinstance(fragment_id, str) or not fragment_id.strip():
                raise SemanticUnitExtractionError("inline fragment id must be a nonempty string")
            if fragment_id in seen_fragment_ids:
                raise SemanticUnitExtractionError("inline fragment ids must be unique")
            seen_fragment_ids.add(fragment_id)
            fragment_type = fragment.get("type")
            if fragment_type not in {"text", "comment", "opaque", "break", "element"}:
                raise SemanticUnitExtractionError("inline fragment type is invalid")
            if fragment_type in {"text", "comment"} and not isinstance(fragment.get("text"), str):
                raise SemanticUnitExtractionError("text or comment fragment text must be a string")
            if fragment_type in {"opaque", "break", "element"} and not isinstance(fragment.get("tag"), str):
                raise SemanticUnitExtractionError("inline fragment tag must be a string")
            if fragment_type == "element":
                children = fragment.get("children")
                if not isinstance(children, list):
                    raise SemanticUnitExtractionError("element fragment children must be a list")
                cls._validate_fragments(children, seen_fragment_ids)

    @classmethod
    def _flatten_visible_text(
        cls, fragments: list[object]
    ) -> tuple[str, tuple[tuple[str, int, int], ...]]:
        pieces: list[str] = []
        segments: list[tuple[str, int, int]] = []
        offset = 0

        def walk(items: list[object]) -> None:
            nonlocal offset
            for fragment in items:
                fragment_mapping = fragment
                if fragment_mapping["type"] == "text":
                    text = fragment_mapping["text"]
                    pieces.append(text)
                    if text:
                        segments.append(
                            (fragment_mapping["id"], offset, offset + len(text))
                        )
                    offset += len(text)
                elif fragment_mapping["type"] == "element":
                    walk(fragment_mapping["children"])

        walk(fragments)
        return "".join(pieces), tuple(segments)

    @staticmethod
    def _inline_spans_for_unit(
        segments: Sequence[tuple[str, int, int]], source_start: int, source_end: int
    ) -> tuple[SemanticInlineSpan, ...]:
        spans: list[SemanticInlineSpan] = []
        for inline_id, segment_start, segment_end in segments:
            overlap_start = max(source_start, segment_start)
            overlap_end = min(source_end, segment_end)
            if overlap_start >= overlap_end:
                continue
            spans.append(
                SemanticInlineSpan(
                    inline_id=inline_id,
                    source_start=overlap_start,
                    source_end=overlap_end,
                    unit_start=overlap_start - source_start,
                    unit_end=overlap_end - source_start,
                )
            )
        return tuple(spans)

    def _segment_spans(self, text: str, language_base: str) -> tuple[tuple[int, int], ...]:
        if language_base in {"zh", "ja", "ko"}:
            return self._cjk_spans(text)
        if language_base == "ru" and self._capabilities.razdel_enabled:
            return tuple((sentence.start, sentence.stop) for sentence in sentenize(text))
        return self._legacy_russian.spans(text)

    def _segmenter_id(self, language_base: str) -> str:
        if language_base in {"zh", "ja", "ko"}:
            return self.CJK_SEGMENTER_ID
        if language_base == "ru":
            return (
                self.RAZDEL_SEGMENTER_ID
                if self._capabilities.razdel_enabled
                else LegacyRussianSegmenter.VERSION
            )
        return self.LEGACY_ALPHABETIC_SEGMENTER_ID

    @staticmethod
    def _language_base(language: object) -> str:
        if not isinstance(language, str) or not language.strip():
            raise SemanticUnitExtractionError("language must be a nonempty string")
        return language.strip().replace("_", "-").split("-", 1)[0].casefold()

    @staticmethod
    def _cjk_spans(text: str) -> tuple[tuple[int, int], ...]:
        spans: list[tuple[int, int]] = []
        start = 0
        index = 0
        closers = frozenset('”’」』）》】')
        while index < len(text):
            if text[index] not in "。！？":
                index += 1
                continue
            stop = index + 1
            while stop < len(text) and text[stop] in "。！？":
                stop += 1
            while stop < len(text) and text[stop] in closers:
                stop += 1
            spans.append((start, stop))
            start = stop
            index = stop
        if start < len(text):
            spans.append((start, len(text)))
        return tuple(spans)

    @staticmethod
    def _trim_span(text: str, start: int, end: int) -> tuple[int, int] | None:
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        return (start, end) if start < end else None

    @staticmethod
    def _normalize_text(text: str) -> str:
        return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip().casefold()

    @staticmethod
    def _block_kind(block: Mapping[str, object]) -> str:
        """Use a nonempty role first, then a nonempty tag, otherwise ``block``."""
        role = block.get("role")
        if isinstance(role, str) and role.strip():
            return role.strip()
        tag = block.get("tag")
        if isinstance(tag, str) and tag.strip():
            return tag.strip()
        return "block"
