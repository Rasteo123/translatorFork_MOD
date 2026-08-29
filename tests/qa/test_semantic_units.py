"""Behavioral contracts for stable semantic-unit extraction from EPUB payloads."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from gemini_translator.qa.capabilities import QaCapabilitySettings
from gemini_translator.qa.models import (
    QaModelValidationError,
    SemanticInlineSpan,
    SemanticUnit,
)
from gemini_translator.qa.semantic_units import (
    SemanticUnitExtractionError,
    SemanticUnitExtractor,
)
from gemini_translator.utils.epub_json import (
    build_html_document_model,
    build_translation_payload,
)


FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "qa" / "segmentation_cases.json"
CASES = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["cases"]


def _visible_text(block):
    parts = []

    def walk(fragments):
        for fragment in fragments:
            if fragment["type"] == "text":
                parts.append(fragment["text"])
            elif fragment["type"] == "element":
                walk(fragment["children"])

    walk(block["inlines"])
    return "".join(parts)


def _span(inline_id="i-1", source_start=0, source_end=5, unit_start=0, unit_end=5):
    return SemanticInlineSpan(
        inline_id=inline_id,
        source_start=source_start,
        source_end=source_end,
        unit_start=unit_start,
        unit_end=unit_end,
    )


def _unit(unit_id="u-1", ordinal=0, document_id="doc"):
    return SemanticUnit(
        unit_id=unit_id,
        document_id=document_id,
        block_id="b-1",
        ordinal=ordinal,
        text="Hello",
        normalized_text="hello",
        source_start=0,
        source_end=5,
        kind="paragraph",
        inline_spans=(_span(),),
    )


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_fixture_cases_extract_expected_text_and_parent_block(case):
    """Dropping a literary segmentation rule or block identity breaks this contract."""
    units = SemanticUnitExtractor(QaCapabilitySettings()).extract(
        case["payload"], case["language"]
    )

    assert [(unit.block_id, unit.text) for unit in units] == [
        (expected["block_id"], expected["text"]) for expected in case["expected"]
    ]


def test_cjk_units_are_repeatable_across_locale_variants_and_keep_stable_ids():
    """Using locale-specific CJK rules or ordinal identities would break repeatability."""
    payload = next(case["payload"] for case in CASES if case["name"] == "cjk_terminal_punctuation")
    extractor = SemanticUnitExtractor(QaCapabilitySettings())

    first = extractor.extract(payload, "zh")
    second = extractor.extract(payload, "zh-CN")

    assert first == second
    assert all(unit.block_id.startswith("b-") for unit in first)
    assert len({unit.unit_id for unit in first}) == len(first)


@pytest.mark.parametrize(
    "case_name, razdel_enabled",
    [
        ("cjk_terminal_punctuation", True),
        ("russian_dialogue_abbreviation_and_ellipsis", True),
        ("russian_dialogue_abbreviation_and_ellipsis", False),
    ],
)
def test_extraction_never_mutates_payload(case_name, razdel_enabled):
    """Mutating EPUB JSON while flattening fragments would corrupt later rendering."""
    payload = deepcopy(next(case["payload"] for case in CASES if case["name"] == case_name))
    before = deepcopy(payload)

    SemanticUnitExtractor(QaCapabilitySettings(razdel_enabled=razdel_enabled)).extract(
        payload, next(case["language"] for case in CASES if case["name"] == case_name)
    )

    assert payload == before


def test_offsets_reconstruct_exact_visible_ranges_after_boundary_trim():
    """Changing offsets after recursive flattening would lose the original visible slice."""
    payload = {
        "document_id": "chapter-offsets",
        "blocks": [
            {
                "id": "b-offsets",
                "tag": "p",
                "role": "paragraph",
                "inlines": [
                    {"id": "i-offsets-1", "type": "text", "text": "  Он "},
                    {
                        "id": "i-offsets-em", "type": "element", "tag": "em", "role": None,
                        "children": [
                            {"id": "i-offsets-2", "type": "text", "text": "вернулся"}
                        ],
                    },
                    {"id": "i-offsets-3", "type": "text", "text": ".  Потом ушёл.  "},
                ],
            }
        ],
    }

    units = SemanticUnitExtractor(QaCapabilitySettings()).extract(payload, "ru")
    visible = _visible_text(payload["blocks"][0])

    assert [unit.text for unit in units] == ["Он вернулся.", "Потом ушёл."]
    assert [visible[unit.source_start : unit.source_end] for unit in units] == [
        unit.text for unit in units
    ]
    assert units[0].normalized_text == "он вернулся."


def test_inline_spans_map_trimmed_sentence_to_each_nested_text_fragment():
    """Losing nested fragment boundaries would make later structural repair unsafe."""
    payload = {
        "document_id": "chapter-inline-spans",
        "blocks": [
            {
                "id": "b-inline-spans",
                "tag": "p",
                "role": "paragraph",
                "inlines": [
                    {"id": "i-before", "type": "text", "text": " Alpha "},
                    {
                        "id": "i-wrapper", "type": "element", "tag": "em", "role": None,
                        "children": [
                            {"id": "i-nested", "type": "text", "text": "Beta"}
                        ],
                    },
                    {"id": "i-after", "type": "text", "text": " gamma."},
                ],
            }
        ],
    }

    (unit,) = SemanticUnitExtractor(QaCapabilitySettings()).extract(payload, "en")

    assert unit.text == "Alpha Beta gamma."
    assert unit.inline_spans == (
        _span("i-before", 1, 7, 0, 6),
        _span("i-nested", 7, 11, 6, 10),
        _span("i-after", 11, 18, 10, 17),
    )


def test_kind_uses_trimmed_role_then_trimmed_tag_then_block_fallback():
    """Whitespace-only role or tag must not become an unstable semantic kind."""
    payload = {
        "document_id": "chapter-kinds",
        "blocks": [
            {"id": "b-role", "role": "  dialogue  ", "tag": "  p  ", "inlines": [
                {"id": "i-role", "type": "text", "text": "Role."}
            ]},
            {"id": "b-tag", "role": "  ", "tag": "  blockquote  ", "inlines": [
                {"id": "i-tag", "type": "text", "text": "Tag."}
            ]},
            {"id": "b-fallback", "role": "  ", "tag": "  ", "inlines": [
                {"id": "i-fallback", "type": "text", "text": "Fallback."}
            ]},
        ],
    }

    units = SemanticUnitExtractor(QaCapabilitySettings()).extract(payload, "en")

    assert [unit.kind for unit in units] == ["dialogue", "blockquote", "block"]


def test_real_epub_payload_contract_preserves_nested_inline_map():
    """Reading ad-hoc JSON instead of the actual EPUB payload would miss contract drift."""
    document_model = build_html_document_model(
        "<html><body><p>Hello <em>bright</em> world.</p></body></html>",
        document_id="chapter-real-contract",
    )
    payload = build_translation_payload(document_model)

    (unit,) = SemanticUnitExtractor(QaCapabilitySettings()).extract(payload, "en")

    assert unit.text == "Hello bright world."
    assert [span.inline_id for span in unit.inline_spans] == [
        fragment["id"]
        for fragment in (
            payload["blocks"][0]["inlines"][0],
            payload["blocks"][0]["inlines"][1]["children"][0],
            payload["blocks"][0]["inlines"][2],
        )
    ]
    assert unit.inline_spans[-1].unit_end == len(unit.text)


def test_segmenter_choice_changes_cache_identity_and_unit_ids_not_epub_text():
    """Collapsing Razdel and legacy identity would reuse incompatible cached embeddings."""
    payload = next(
        case["payload"]
        for case in CASES
        if case["name"] == "russian_dialogue_abbreviation_and_ellipsis"
    )
    razdel = SemanticUnitExtractor(QaCapabilitySettings(razdel_enabled=True))
    legacy = SemanticUnitExtractor(QaCapabilitySettings(razdel_enabled=False))

    razdel_units = razdel.extract(payload, "ru")
    legacy_units = legacy.extract(payload, "ru")

    assert [unit.text for unit in razdel_units] == [unit.text for unit in legacy_units]
    assert [unit.unit_id for unit in razdel_units] != [unit.unit_id for unit in legacy_units]
    assert razdel.preprocessing_identity != legacy.preprocessing_identity


def test_windows_include_all_contiguous_sizes_grouped_by_document():
    """Joining units across a chapter boundary or skipping a window would corrupt alignment."""
    extractor = SemanticUnitExtractor(QaCapabilitySettings())
    document_one = extractor.extract(
        {
            "document_id": "chapter-one",
            "blocks": [
                {"id": "b-one", "tag": "p", "role": "paragraph", "inlines": [
                    {"id": "i-one", "type": "text", "text": "One. Two. Three."}
                ]}
            ],
        },
        "en",
    )
    document_two = extractor.extract(
        {
            "document_id": "chapter-two",
            "blocks": [
                {"id": "b-two", "tag": "p", "role": "paragraph", "inlines": [
                    {"id": "i-two", "type": "text", "text": "Four. Five."}
                ]}
            ],
        },
        "en",
    )

    windows = extractor.windows(document_one + document_two, max_size=3)

    assert [window.text for window in windows] == [
        "One.", "One. Two.", "One. Two. Three.",
        "Two.", "Two. Three.", "Three.",
        "Four.", "Four. Five.", "Five.",
    ]
    assert all("Three. Four." not in window.text for window in windows)


def test_windows_reject_a_non_positive_max_size():
    """Accepting an empty window size would silently produce an invalid alignment input."""
    with pytest.raises(ValueError, match="max_size"):
        SemanticUnitExtractor(QaCapabilitySettings()).windows((), max_size=0)


@pytest.mark.parametrize(
    "units, error",
    [
        ((_unit("u-shared", 0), _unit("u-shared", 1)), "unit_id"),
        ((_unit("u-first", 0), _unit("u-second", 0)), "ordinal"),
    ],
)
def test_windows_reject_duplicate_ids_and_ordinals_within_a_document(units, error):
    """Ambiguous window ordering or identity must fail before alignment starts."""
    with pytest.raises(ValueError, match=error):
        SemanticUnitExtractor(QaCapabilitySettings()).windows(units)


@pytest.mark.parametrize(
    "factory",
    [
        lambda: SemanticInlineSpan("", 0, 1, 0, 1),
        lambda: SemanticInlineSpan("i", True, 1, 0, 1),
        lambda: SemanticInlineSpan("i", 2, 1, 0, 1),
        lambda: SemanticInlineSpan("i", 1, 1, 0, 0),
        lambda: SemanticInlineSpan("i", 0, 2, 0, 1),
        lambda: SemanticUnit(
            unit_id="",
            document_id="doc",
            block_id="b",
            ordinal=0,
            text="Text",
            normalized_text="text",
            source_start=0,
            source_end=4,
            kind="paragraph",
            inline_spans=(_span(source_end=4, unit_end=4),),
        ),
        lambda: SemanticUnit(
            unit_id="u",
            document_id="doc",
            block_id="b",
            ordinal=-1,
            text="Text",
            normalized_text="text",
            source_start=0,
            source_end=4,
            kind="paragraph",
            inline_spans=(_span(source_end=4, unit_end=4),),
        ),
        lambda: SemanticUnit(
            unit_id="u",
            document_id="doc",
            block_id="b",
            ordinal=0,
            text="Text",
            normalized_text="text",
            source_start=0,
            source_end=4,
            kind="paragraph",
            inline_spans=(_span(source_start=1, source_end=4, unit_start=0, unit_end=3),),
        ),
    ],
)
def test_semantic_models_reject_empty_or_invalid_typed_ranges(factory):
    """Relaxing immutable model checks would persist unusable structural repair maps."""
    with pytest.raises(QaModelValidationError):
        factory()


def test_semantic_unit_rejects_adjacent_spans_with_the_same_inline_id():
    """One producer text leaf may contribute at most one repair span per unit."""
    with pytest.raises(QaModelValidationError, match="inline_id"):
        SemanticUnit(
            unit_id="u",
            document_id="doc",
            block_id="b",
            ordinal=0,
            text="Hello",
            normalized_text="hello",
            source_start=0,
            source_end=5,
            kind="paragraph",
            inline_spans=(
                _span("i-shared", 0, 2, 0, 2),
                _span("i-shared", 2, 5, 2, 5),
            ),
        )


def test_semantic_window_rejects_empty_or_duplicate_unit_ids():
    """A window without unique unit identities cannot be aligned deterministically."""
    from gemini_translator.qa.models import SemanticWindow

    with pytest.raises(QaModelValidationError):
        SemanticWindow(unit_ids=(), text="text")
    with pytest.raises(QaModelValidationError):
        SemanticWindow(unit_ids=("u-1", "u-1"), text="text")
    with pytest.raises(QaModelValidationError):
        SemanticWindow(unit_ids=("u-1",), text="")


@pytest.mark.parametrize(
    "payload",
    [
        {"document_id": "", "blocks": []},
        {"document_id": "chapter", "blocks": "not-a-list"},
        {"document_id": "chapter", "blocks": [{"id": "", "inlines": []}]},
        {
            "document_id": "chapter",
            "blocks": [
                {"id": "b-duplicate", "inlines": []},
                {"id": "b-duplicate", "inlines": []},
            ],
        },
        {
            "document_id": "chapter",
            "blocks": [
                {"id": "b-bad-inline", "inlines": [{"id": "i-bad", "type": "unknown"}]}
            ],
        },
        {
            "document_id": "chapter",
            "blocks": [
                {
                    "id": "b-first",
                    "inlines": [
                        {
                            "id": "i-wrapper",
                            "type": "element",
                            "tag": "em",
                            "children": [
                                {"id": "i-duplicate", "type": "text", "text": "One."}
                            ],
                        }
                    ],
                },
                {
                    "id": "b-second",
                    "inlines": [
                        {"id": "i-duplicate", "type": "text", "text": "Two."}
                    ],
                },
            ],
        },
    ],
)
def test_malformed_stable_payload_identity_is_rejected(payload):
    """Replacing invalid stable IDs with ordinal fallback would make cache keys unsafe."""
    with pytest.raises(SemanticUnitExtractionError):
        SemanticUnitExtractor(QaCapabilitySettings()).extract(payload, "en")
