"""Behavioral contracts for bounded semantic alignment."""

import json
from dataclasses import FrozenInstanceError, replace
import math
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

from gemini_translator.qa.alignment import AlignmentCapacityError, MonotonicAligner
from gemini_translator.qa.models import (
    AlignmentConfig,
    AlignmentResult,
    AlignmentSpan,
    EmbeddedUnits,
    GapCandidate,
    QaModelValidationError,
    SemanticInlineSpan,
    SemanticUnit,
)


_CASES = json.loads((Path(__file__).parents[1] / "fixtures/qa/alignment_cases.json").read_text())


def _units(prefix, vectors, document_id, *, texts=None, blocks=None):
    texts = tuple(texts) if texts is not None else ("visible",) * len(vectors)
    blocks = tuple(blocks) if blocks is not None else tuple(range(len(texts)))
    units = tuple(
        SemanticUnit(
            unit_id=f"{prefix}{index}", document_id=document_id, block_id=f"b{blocks[index]}",
            ordinal=index, text=text, normalized_text=text.strip(), source_start=0,
            source_end=len(text), kind="paragraph",
            inline_spans=(SemanticInlineSpan(f"i{index}", 0, len(text), 0, len(text)),),
        )
        for index, text in enumerate(texts)
    )
    return EmbeddedUnits(document_id=document_id, units=units, vectors=np.asarray(vectors, dtype=np.float32))


def _case(name):
    data = _CASES[name]
    return _units("s", data["source"], "source-doc"), _units("t", data["target"], "target-doc")


def _config(**changes):
    # The volume and orphan terms are opt-in here: every case that predates them
    # isolates one scoring rule, and their own cases switch them on explicitly.
    values = dict(max_span_size=3, max_drift_units=4, max_cells=10_000, merge_penalty=0.05,
                  gap_penalty=1.2, anchor_similarity=0.95, volume_penalty=0.0,
                  orphan_penalty=0.0)
    values.update(changes)
    return AlignmentConfig(**values)


@pytest.mark.parametrize(("case_name", "operations"), [
    ("one_to_one", ("1:1",)), ("source_split_in_translation", ("1:2",)),
    ("one_to_three", ("1:3",)), ("source_merge_in_translation", ("2:1",)),
    ("two_to_two", ("2:2",)), ("two_to_three", ("2:3",)),
    ("three_to_one", ("3:1",)), ("three_to_two", ("3:2",)),
    ("three_to_three_reordering", ("3:3",)), ("local_reordering", ("2:2",)),
])
def test_alignment_selects_expected_contiguous_operations(case_name, operations):
    """Removing span transitions would turn valid split/merge mappings into gaps."""
    result = MonotonicAligner(_config()).align(*_case(case_name))
    assert tuple(span.operation for span in result.spans) == operations


def test_local_reordering_cannot_exceed_configured_window():
    """Allowing an implicit remote permutation would defeat monotonic chapter alignment."""
    result = MonotonicAligner(_config(max_span_size=2)).align(
        *_case("three_to_three_reordering")
    )

    assert "3:3" not in {span.operation for span in result.spans}
    assert all(
        len(span.source_unit_ids) <= 2 and len(span.target_unit_ids) <= 2
        for span in result.spans
    )


def test_span_mean_is_normalized_and_weighted_by_visible_characters():
    """Counting whitespace or concatenating vectors would change the 1:2 cosine score."""
    source = _units("s", [[1.0, 3.0]], "source-doc", texts=("meaning",))
    target = _units(
        "t", [[1.0, 0.0], [0.0, 1.0]], "target-doc", texts=("x", "   yyy")
    )

    result = MonotonicAligner(_config()).align(source, target)

    assert tuple(span.operation for span in result.spans) == ("1:2",)
    assert result.spans[0].similarity == pytest.approx(1.0, abs=1e-6)


def test_zero_weighted_merge_vector_skips_only_that_transition():
    """Opposite valid embeddings must not abort an otherwise valid 1:1 alignment path."""
    source = _units("s", [[1.0, 0.0], [-1.0, 0.0]], "source-doc")
    target = _units("t", [[1.0, 0.0], [-1.0, 0.0]], "target-doc")

    result = MonotonicAligner(_config(max_span_size=2)).align(source, target)

    assert tuple(span.operation for span in result.spans) == ("1:1", "1:1")
    assert tuple(span.similarity for span in result.spans) == pytest.approx((1.0, 1.0))


def test_merge_penalty_is_added_to_one_minus_cosine():
    """Ignoring merge_penalty would always prefer a perfect aggregate over a cheap gap."""
    source, target = _case("source_split_in_translation")

    merged = MonotonicAligner(
        _config(max_span_size=2, merge_penalty=0.05, gap_penalty=0.4)
    ).align(source, target)
    split_by_gap = MonotonicAligner(
        _config(max_span_size=2, merge_penalty=0.6, gap_penalty=0.4)
    ).align(source, target)

    assert tuple(span.operation for span in merged.spans) == ("1:2",)
    assert {span.operation for span in split_by_gap.spans} == {"1:1", "0:1"}


def test_gap_cost_adds_documented_local_penalty():
    """Dropping local_gap_penalty would select two gaps despite the configured policy."""
    source = _units("s", [[1.0, 0.0]], "source-doc")
    target = _units("t", [[0.0, 1.0]], "target-doc")

    gaps = MonotonicAligner(
        _config(max_span_size=1, gap_penalty=0.4, local_gap_penalty=0.0)
    ).align(source, target)
    match = MonotonicAligner(
        _config(max_span_size=1, gap_penalty=0.4, local_gap_penalty=0.2)
    ).align(source, target)

    assert len(gaps.gaps) == 2
    assert tuple(span.operation for span in match.spans) == ("1:1",)


def test_source_gap_in_middle_is_repairable_with_two_adjacent_anchors():
    """Dropping anchor checks would make edge and unanchored content auto-repairable."""
    result = MonotonicAligner(_config(merge_penalty=2.0)).align(*_case("missing_middle"))
    gap = result.gaps[0]
    assert gap.side == "source"
    assert gap.source_unit_ids == ("s1",)
    assert gap.target_unit_ids == ()
    assert gap.left_anchor is not None and gap.right_anchor is not None
    assert gap.repairable is True
    assert gap.signals == ("missing_in_target",)


def test_edge_source_gap_and_target_gap_are_report_only():
    """A repairable flag on an edge or target-only gap would authorize the wrong repair."""
    edge = MonotonicAligner(_config(merge_penalty=2.0)).align(*_case("missing_first")).gaps[0]
    assert edge.side == "source" and edge.repairable is False
    assert edge.signals == ("missing_in_target",)
    source, target = _case("missing_first")
    reverse = MonotonicAligner(_config(merge_penalty=2.0)).align(target, source).gaps[0]
    assert reverse.side == "target" and reverse.repairable is False
    assert reverse.signals == ("addition",)


def test_last_source_gap_is_report_only_and_keeps_only_source_ids():
    """Using a distant left match as both anchors would authorize an edge insertion."""
    gap = MonotonicAligner(_config(max_span_size=1, merge_penalty=2.0)).align(
        *_case("missing_last")
    ).gaps[0]

    assert gap.side == "source"
    assert gap.source_unit_ids == ("s1",)
    assert gap.target_unit_ids == ()
    assert gap.left_anchor is not None
    assert gap.right_anchor is None
    assert gap.repairable is False
    assert gap.signals == ("missing_in_target",)


def test_target_only_middle_content_is_an_addition_even_with_two_anchors():
    """Treating a target-only span as a source omission would repair the wrong document."""
    gap = MonotonicAligner(
        _config(max_span_size=1, gap_penalty=0.4, anchor_similarity=0.95)
    ).align(*_case("target_addition_middle")).gaps[0]

    assert gap.side == "target"
    assert gap.source_unit_ids == ()
    assert gap.target_unit_ids == ("t1",)
    assert gap.left_anchor is not None and gap.right_anchor is not None
    assert gap.repairable is False
    assert gap.signals == ("addition",)


def test_consecutive_source_gaps_merge_into_one_candidate():
    """Emitting one repair per unit would duplicate context and destabilize insertion order."""
    result = MonotonicAligner(
        _config(max_span_size=1, gap_penalty=0.4, anchor_similarity=0.95)
    ).align(*_case("two_missing_middle"))

    assert tuple(span.operation for span in result.spans) == ("1:1", "1:0", "1:0", "1:1")
    assert len(result.gaps) == 1
    assert result.gaps[0].source_unit_ids == ("s1", "s2")
    assert result.gaps[0].repairable is True


def test_low_similarity_immediate_anchor_keeps_source_gap_report_only():
    """Skipping the threshold would make a weak neighborhood sufficient for auto-repair."""
    gap = MonotonicAligner(
        _config(max_span_size=1, gap_penalty=0.4, anchor_similarity=0.9)
    ).align(*_case("missing_middle_low_right_anchor")).gaps[0]

    assert gap.left_anchor is not None
    assert gap.right_anchor is None
    assert gap.repairable is False


def test_embedded_units_defensively_normalizes_to_readonly_float32_matrix():
    """Returning a mutable or non-unit provider matrix would corrupt later similarity scores."""
    embedded = _units("s", [[3, 4], [0, 2]], "doc")
    assert embedded.vectors.dtype == np.float32
    assert embedded.vectors.flags.c_contiguous and not embedded.vectors.flags.writeable
    np.testing.assert_allclose(np.linalg.norm(embedded.vectors, axis=1), [1.0, 1.0])
    with pytest.raises(ValueError):
        embedded.vectors[0, 0] = 0
    with pytest.raises(FrozenInstanceError):
        embedded.document_id = "other"


def test_embedded_units_owns_a_snapshot_of_the_provider_matrix():
    """Keeping a view would let provider reuse mutate an accepted embedding batch."""
    provider_vectors = np.array([[3.0, 4.0], [0.0, 2.0]], dtype=np.float64, order="F")
    embedded = _units("s", provider_vectors, "doc")
    before = embedded.vectors.copy()

    provider_vectors[:] = 99.0

    np.testing.assert_array_equal(embedded.vectors, before)


@pytest.mark.parametrize(
    "vectors",
    [
        np.array([1.0, 0.0]),
        np.empty((2, 0), dtype=np.float32),
        np.ones((1, 2), dtype=np.float32),
        np.array([[True, False], [False, True]]),
        np.array([[1 + 0j, 0j], [0j, 1 + 0j]]),
        np.array([[1, object()], [0, 1]], dtype=object),
        np.array([["1", "0"], ["0", "1"]], dtype="U"),
        np.array([[b"1", b"0"], [b"0", b"1"]], dtype="S"),
        np.array([[math.nan, 0.0], [0.0, 1.0]]),
        np.array([[math.inf, 0.0], [0.0, 1.0]]),
        np.zeros((2, 2), dtype=np.float32),
    ],
)
def test_embedded_units_rejects_malformed_vector_arrays(vectors):
    """Coercing unsafe matrices would move adapter corruption into the DP loop."""
    valid = _units("s", [[1, 0], [0, 1]], "doc")

    with pytest.raises(QaModelValidationError):
        EmbeddedUnits(document_id="doc", units=valid.units, vectors=vectors)


@pytest.mark.parametrize("mutation", ["cross_document", "duplicate_id", "nonmonotonic"])
def test_embedded_units_rejects_cross_document_duplicate_or_nonmonotonic_units(mutation):
    """Unstable unit identity would make anchors refer to a different document position."""
    valid = _units("s", [[1, 0], [0, 1]], "doc")
    units = list(valid.units)
    if mutation == "cross_document":
        units[1] = replace(units[1], document_id="other")
    elif mutation == "duplicate_id":
        units[1] = replace(units[1], unit_id=units[0].unit_id)
    else:
        units[1] = replace(units[1], ordinal=0)

    with pytest.raises(QaModelValidationError):
        EmbeddedUnits(document_id="doc", units=tuple(units), vectors=valid.vectors)


def test_config_and_embedded_units_reject_invalid_contracts():
    """Weak validation would let an unbounded or mismatched vector batch reach DP."""
    with pytest.raises(QaModelValidationError):
        AlignmentConfig(max_cells=0)
    source, _ = _case("one_to_one")
    with pytest.raises(QaModelValidationError):
        EmbeddedUnits(document_id="source-doc", units=source.units, vectors=np.zeros((1, 2)))


@pytest.mark.parametrize(
    "changes",
    [
        {"max_span_size": True},
        {"max_drift_units": True},
        {"max_cells": True},
        {"merge_penalty": True},
        {"gap_penalty": math.nan},
        {"local_gap_penalty": math.inf},
        {"anchor_similarity": -math.inf},
        {"local_gap_penalty": -0.01},
        {"operation_order": ["1:1", "1:0", "0:1"]},
        {"max_span_size": 1, "operation_order": ("1:1", "1:0")},
        {"max_span_size": 1, "operation_order": ("1:1", "1:0", "0:1", "2:2")},
    ],
)
def test_alignment_config_rejects_bool_nonfinite_and_invalid_operation_policies(changes):
    """Permissive scoring values or operation sets would weaken resource and path bounds."""
    with pytest.raises(QaModelValidationError):
        AlignmentConfig(**changes)


def test_alignment_config_is_frozen_and_builds_order_for_selected_max_span():
    """A stale max-span order either enables forbidden windows or rejects valid small configs."""
    config = AlignmentConfig(max_span_size=1)

    assert config.operation_order == ("1:1", "1:0", "0:1")
    with pytest.raises(FrozenInstanceError):
        config.max_cells = 2


def test_aligner_rejects_embedding_dimension_mismatch_before_similarity():
    """Letting NumPy raise leaks an untyped adapter failure from the alignment boundary."""
    source = _units("s", [[1.0, 0.0]], "source-doc")
    target = _units("t", [[1.0, 0.0, 0.0]], "target-doc")

    with pytest.raises(QaModelValidationError, match="dimension"):
        MonotonicAligner(_config()).align(source, target)


def test_source_and_target_vector_caches_stay_separate_for_same_document_id():
    """A chapter-shared document ID must not alias source and target span vectors."""
    source = _units("s", [[1.0, 0.0]], "chapter-1")
    target = _units("t", [[0.0, 1.0]], "chapter-1")

    result = MonotonicAligner(_config(max_span_size=1)).align(source, target)

    assert tuple(span.operation for span in result.spans) == ("1:1",)
    assert result.spans[0].similarity == pytest.approx(0.0, abs=1e-7)


@pytest.mark.parametrize(
    "values",
    [
        (("s0",), (), 0.5, "1:0"),
        (("s0", "s0"), ("t0",), 0.5, "2:1"),
        (("s0", "s1", "s2", "s3"), ("t0",), 0.5, "4:1"),
    ],
)
def test_alignment_span_rejects_invalid_gap_score_duplicates_and_oversized_operations(values):
    """Malformed spans make cost and monotonic coverage impossible to interpret."""
    with pytest.raises(QaModelValidationError):
        AlignmentSpan(*values)


def test_gap_candidate_rejects_invalid_identity_anchor_and_repair_contracts():
    """A candidate must not authorize repair without a source gap and two match anchors."""
    left = AlignmentSpan(("s0",), ("t0",), 1.0, "1:1")
    right = AlignmentSpan(("s2",), ("t1",), 1.0, "1:1")
    gap_span = AlignmentSpan(("s1",), (), 0.0, "1:0")
    valid_id = "gap-" + "a" * 20

    invalid_builders = (
        lambda: GapCandidate("not-a-sha", "source", ("s1",), (), left, right, True, ("missing_in_target",)),
        lambda: GapCandidate(valid_id, "source", ("s1",), (), gap_span, right, True, ("missing_in_target",)),
        lambda: GapCandidate(valid_id, "source", ("s1",), (), left, None, True, ("missing_in_target",)),
        lambda: GapCandidate(valid_id, "target", (), ("t1",), left, right, True, ("addition",)),
        lambda: GapCandidate(valid_id, "source", ("s1",), (), left, right, True, ("addition",)),
        lambda: GapCandidate(valid_id, "source", ("s1",), (), left, right, True, ("missing_in_target", "missing_in_target")),
    )
    for build in invalid_builders:
        with pytest.raises(QaModelValidationError):
            build()


def test_alignment_result_rejects_gaps_not_matching_immediate_span_groups():
    """Detached or duplicated candidates could point repair at the wrong local neighborhood."""
    left = AlignmentSpan(("s0",), ("t0",), 1.0, "1:1")
    gap_span = AlignmentSpan(("s1",), (), 0.0, "1:0")
    right = AlignmentSpan(("s2",), ("t1",), 1.0, "1:1")
    candidate = GapCandidate(
        "gap-" + "a" * 20, "source", ("s1",), (), left, right, True,
        ("missing_in_target",),
    )

    valid = AlignmentResult((left, gap_span, right), (candidate,), 3)
    assert valid.gaps == (candidate,)
    with pytest.raises(QaModelValidationError):
        AlignmentResult((left, gap_span, right), (), 3)
    with pytest.raises(QaModelValidationError):
        AlignmentResult((left, gap_span, right), (candidate, candidate), 3)
    distant = AlignmentSpan(("s9",), ("t9",), 1.0, "1:1")
    detached = replace(candidate, left_anchor=distant, repairable=False)
    with pytest.raises(QaModelValidationError):
        AlignmentResult((left, gap_span, right), (detached,), 3)
    duplicate_source = AlignmentSpan(("s0",), ("t2",), 1.0, "1:1")
    with pytest.raises(QaModelValidationError):
        AlignmentResult((left, duplicate_source), (), 2)


def test_capacity_is_preflighted_and_large_banded_run_is_deterministic():
    """Allocating before the cap check could exhaust memory on long, uneven chapters."""
    vectors = np.tile(np.array([[1.0, 0.0]], dtype=np.float32), (1000, 1))
    source = _units("s", vectors, "source-doc")
    target = _units("t", np.vstack((vectors, np.tile([[1.0, 0.0]], (100, 1)))), "target-doc")
    config = _config(max_cells=230_000, max_drift_units=4, anchor_similarity=0.5)
    first = MonotonicAligner(config).align(source, target)
    second = MonotonicAligner(config).align(source, target)
    assert first.visited_cells <= config.max_cells
    assert first.visited_cells < (len(source.units) + 1) * (len(target.units) + 1)
    assert first == second
    with pytest.raises(AlignmentCapacityError) as caught:
        MonotonicAligner(_config(max_cells=5, max_drift_units=4)).align(source, target)
    assert caught.value.required_cells > caught.value.max_cells == 5
    with pytest.raises(FrozenInstanceError):
        caught.value.required_cells = 1


def test_capacity_preflight_runs_before_any_similarity_work(monkeypatch):
    """A tiny cap must fail before allocating similarity or entering DP transitions."""
    source, target = _case("one_to_one")
    called = False

    def forbidden_similarity(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("similarity must not run before capacity rejection")

    monkeypatch.setattr(MonotonicAligner, "_similarity", forbidden_similarity)
    with pytest.raises(AlignmentCapacityError):
        MonotonicAligner(_config(max_cells=1)).align(source, target)
    assert called is False


def test_ties_are_deterministic_and_candidate_ids_are_stable():
    """Unordered equal-cost choices would make repair candidates change between runs."""
    source = _units("s", [[1, 0], [1, 0], [1, 0]], "source-doc")
    target = _units("t", [[1, 0], [1, 0]], "target-doc")
    config = _config(merge_penalty=2.0)
    first = MonotonicAligner(config).align(source, target)
    second = MonotonicAligner(config).align(source, target)
    assert first == second
    assert all(candidate.candidate_id.startswith("gap-") for candidate in first.gaps)


def test_candidate_id_is_an_exact_stable_sha_prefix():
    """Process-random hashes would change persisted candidate identity between runs."""
    gap = MonotonicAligner(_config(merge_penalty=2.0)).align(
        *_case("missing_middle")
    ).gaps[0]

    assert gap.candidate_id == "gap-a3ff14a269d45b0c19a7"


def test_alignment_outputs_are_frozen_value_objects():
    """Mutating a span or candidate after result construction would invalidate its anchors."""
    result = MonotonicAligner(_config(merge_penalty=2.0)).align(*_case("missing_middle"))

    for value, field, replacement in (
        (result.spans[0], "similarity", 0.0),
        (result.gaps[0], "repairable", False),
        (result, "visited_cells", 0),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(value, field, replacement)


def test_alignment_import_boundary_is_qt_engine_pandas_and_network_free():
    """Importing the pure aligner must not initialize UI, translation, or network stacks."""
    project_root = Path(__file__).parents[2]
    script = f"""
import builtins
import sys
sys.path.insert(0, {str(project_root)!r})
blocked_roots = {{"PyQt5", "PyQt6", "PySide6", "pandas", "requests", "aiohttp", "httpx"}}
real_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name.split('.', 1)[0] in blocked_roots or "translation_engine" in name:
        raise AssertionError(f"forbidden import: {{name}}")
    return real_import(name, *args, **kwargs)
builtins.__import__ = guarded_import
import gemini_translator.qa.alignment
"""

    completed = subprocess.run(
        [sys.executable, "-I", "-c", script],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


# --- missing paragraphs ----------------------------------------------------


_PARAGRAPH = "Достаточно длинная строка перевода для целого абзаца."
_LINE = "Коротко."


def _paragraph_case(text=_PARAGRAPH):
    """Three source paragraphs of two sentences each; the middle one is untranslated."""
    source = _units(
        "s",
        [[1.0, 0.0, 0.0]] * 2 + [[0.0, 1.0, 0.0]] * 2 + [[0.0, 0.0, 1.0]] * 2,
        "source-doc",
        texts=(text,) * 6,
        blocks=(0, 0, 1, 1, 2, 2),
    )
    target = _units(
        "t",
        [[1.0, 0.0, 0.0]] * 2 + [[0.0, 0.0, 1.0]] * 2,
        "target-doc",
        texts=(text,) * 4,
        blocks=(0, 0, 1, 1),
    )
    return source, target


def test_an_untranslated_paragraph_becomes_a_repairable_gap_on_its_own_evidence():
    """Without this the sentences of a dropped paragraph are absorbed by the neighbours."""
    source, target = _paragraph_case()

    blind = MonotonicAligner(_config(orphan_penalty=0.0)).align(source, target)
    aware = MonotonicAligner(
        _config(orphan_drop=0.05, orphan_penalty=2.0, anchor_similarity=0.85)
    ).align(source, target)

    assert blind.gaps == ()
    assert len(aware.gaps) == 1
    gap = aware.gaps[0]
    assert gap.side == "source"
    assert gap.source_unit_ids == ("s2", "s3")
    assert gap.repairable is True


def test_a_short_paragraph_is_never_called_missing_on_similarity_alone():
    """One-word lines drift below any median; treating that as evidence floods the LLM."""
    source, target = _paragraph_case(_LINE)

    result = MonotonicAligner(
        _config(orphan_drop=0.05, orphan_penalty=2.0)
    ).align(source, target)

    assert result.gaps == ()


def test_orphan_evidence_only_charges_the_paragraph_it_was_measured_on():
    """Charging a whole span would drag the neighbouring paragraphs into the gap."""
    source, target = _paragraph_case()

    result = MonotonicAligner(
        _config(orphan_drop=0.05, orphan_penalty=50.0)
    ).align(source, target)

    gapped = tuple(
        unit_id for span in result.spans if span.operation == "1:0"
        for unit_id in span.source_unit_ids
    )
    assert gapped == ("s2", "s3")


def _volume_case():
    """Two source sentences whose translations are split 50/150 instead of 100/100."""
    source = _units(
        "s", [[1.0, 0.0]] * 2, "source-doc", texts=("x" * 100,) * 2
    )
    target = _units(
        "t", [[1.0, 0.0]] * 2, "target-doc", texts=("y" * 50, "y" * 150)
    )
    return source, target


def test_a_span_is_not_free_to_shrink_or_stretch_the_text_it_carries():
    """Ignoring volume lets the search move characters between spans to hide a loss."""
    source, target = _volume_case()

    blind = MonotonicAligner(_config(volume_penalty=0.0)).align(source, target)
    aware = MonotonicAligner(_config(volume_penalty=2.0)).align(source, target)

    assert tuple(span.operation for span in blind.spans) == ("1:1", "1:1")
    assert tuple(span.operation for span in aware.spans) == ("2:2",)


def test_surplus_text_is_charged_more_lightly_than_missing_text():
    """Charging only shortfalls would make dropping any unit look like an improvement."""
    source, target = _volume_case()

    lenient = MonotonicAligner(
        _config(volume_penalty=0.15, volume_surplus_weight=0.0)
    ).align(source, target)
    strict = MonotonicAligner(
        _config(volume_penalty=0.15, volume_surplus_weight=1.0)
    ).align(source, target)

    assert tuple(span.operation for span in lenient.spans) == ("1:1", "1:1")
    assert tuple(span.operation for span in strict.spans) == ("2:2",)


def test_the_shipped_scoring_defaults_are_the_calibrated_ones():
    """These numbers were measured on real chapters; drifting from them silently is a regression."""
    config = AlignmentConfig()

    assert (config.volume_penalty, config.volume_surplus_weight) == (0.5, 0.5)
    assert (config.orphan_drop, config.orphan_penalty, config.orphan_min_chars) == (
        0.04,
        2.0,
        60,
    )
