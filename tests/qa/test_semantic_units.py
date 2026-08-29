"""Behavioral contracts for stable semantic-unit extraction from EPUB payloads."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from gemini_translator.qa.capabilities import QaCapabilitySettings
from gemini_translator.qa.semantic_units import (
    SemanticUnitExtractionError,
    SemanticUnitExtractor,
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
    ],
)
def test_malformed_stable_payload_identity_is_rejected(payload):
    """Replacing invalid stable IDs with ordinal fallback would make cache keys unsafe."""
    with pytest.raises(SemanticUnitExtractionError):
        SemanticUnitExtractor(QaCapabilitySettings()).extract(payload, "en")
