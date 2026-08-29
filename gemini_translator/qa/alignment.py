"""Bounded NumPy semantic alignment for one source/translation document pair."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math

import numpy as np

from .models import AlignmentConfig, AlignmentResult, AlignmentSpan, EmbeddedUnits, GapCandidate, QaModelValidationError


@dataclass(frozen=True, slots=True)
class AlignmentCapacityError(RuntimeError):
    """Typed immutable diagnostics for a bounded alignment that cannot be allocated."""

    required_cells: int
    max_cells: int

    def __post_init__(self) -> None:
        for value, field_name in (
            (self.required_cells, "required_cells"),
            (self.max_cells, "max_cells"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise QaModelValidationError(f"{field_name} must be a positive integer")
        RuntimeError.__init__(
            self,
            f"alignment needs {self.required_cells} band cells, cap is {self.max_cells}",
        )


@dataclass(frozen=True, slots=True)
class _State:
    cost: float
    gaps: int
    window: int
    order: int
    previous: tuple[int, int] | None
    span: AlignmentSpan | None

    @property
    def key(self) -> tuple[float, int, int, int]:
        return (self.cost, self.gaps, self.window, self.order)


class MonotonicAligner:
    """Find deterministic contiguous 1..3-to-1..3 alignments and unit gaps."""

    def __init__(self, config: AlignmentConfig):
        if not isinstance(config, AlignmentConfig):
            raise QaModelValidationError("config must be an AlignmentConfig")
        self.config = config

    def align(self, source: EmbeddedUnits, target: EmbeddedUnits) -> AlignmentResult:
        if not isinstance(source, EmbeddedUnits) or not isinstance(target, EmbeddedUnits):
            raise QaModelValidationError("source and target must be EmbeddedUnits")
        if source.vectors.shape[1] != target.vectors.shape[1]:
            raise QaModelValidationError("source and target embedding dimensions must match")
        n, m = len(source.units), len(target.units)
        band = abs(n - m) + self.config.max_drift_units
        cells = self._preflight_cells(n, m, band)
        if cells > self.config.max_cells:
            raise AlignmentCapacityError(cells, self.config.max_cells)
        states: dict[tuple[int, int], _State] = {(0, 0): _State(0.0, 0, 0, 0, None, None)}
        vectors: dict[tuple[int, int, int], np.ndarray | None] = {}
        order = {operation: index for index, operation in enumerate(self.config.operation_order)}
        for i in range(n + 1):
            low, high = self._row_bounds(i, n, m, band)
            for j in range(low, high + 1):
                if i == 0 and j == 0:
                    continue
                best: _State | None = None
                for source_size, target_size in self._operations():
                    previous = (i - source_size, j - target_size)
                    prior = states.get(previous)
                    if prior is None:
                        continue
                    operation = f"{source_size}:{target_size}"
                    if source_size and target_size:
                        similarity = self._similarity(source, i - source_size, source_size, target, j - target_size, target_size, vectors)
                        if similarity is None:
                            continue
                        cost = 1.0 - similarity + self.config.merge_penalty * (source_size + target_size - 2)
                    else:
                        similarity = 0.0
                        cost = self.config.gap_penalty + self.config.local_gap_penalty
                    span = AlignmentSpan(
                        tuple(unit.unit_id for unit in source.units[i - source_size:i]),
                        tuple(unit.unit_id for unit in target.units[j - target_size:j]),
                        float(similarity), operation,
                    )
                    candidate = _State(prior.cost + cost, prior.gaps + int(not source_size or not target_size), source_size + target_size, order[operation], previous, span)
                    if best is None or candidate.key < best.key:
                        best = candidate
                if best is not None:
                    states[(i, j)] = best
        if (n, m) not in states:
            raise AlignmentCapacityError(cells, self.config.max_cells)
        spans: list[AlignmentSpan] = []
        cursor = (n, m)
        while cursor != (0, 0):
            state = states[cursor]
            assert state.previous is not None and state.span is not None
            spans.append(state.span)
            cursor = state.previous
        spans.reverse()
        stable_spans = tuple(spans)
        return AlignmentResult(stable_spans, self._gaps(stable_spans, source, target), len(states))

    def _operations(self):
        for source_size in range(1, self.config.max_span_size + 1):
            for target_size in range(1, self.config.max_span_size + 1):
                yield source_size, target_size
        yield 1, 0
        yield 0, 1

    @staticmethod
    def _row_bounds(i: int, n: int, m: int, band: int) -> tuple[int, int]:
        expected = i * m / n if n else 0.0
        return max(0, math.ceil(expected - band)), min(m, math.floor(expected + band))

    def _preflight_cells(self, n: int, m: int, band: int) -> int:
        return sum(self._row_bounds(i, n, m, band)[1] - self._row_bounds(i, n, m, band)[0] + 1 for i in range(n + 1))

    @staticmethod
    def _span_vector(
        data: EmbeddedUnits,
        start: int,
        size: int,
        cache: dict[tuple[int, int, int], np.ndarray | None],
    ) -> np.ndarray | None:
        key = (id(data), start, size)
        if key in cache:
            return cache[key]
        weights = np.asarray(
            [max(1, sum(not character.isspace() for character in unit.text))
             for unit in data.units[start:start + size]],
            dtype=np.float64,
        )
        mean = np.average(data.vectors[start:start + size].astype(np.float64), axis=0, weights=weights)
        norm = np.linalg.norm(mean)
        if not np.isfinite(norm) or norm <= np.finfo(np.float32).eps:
            cache[key] = None
            return None
        vector = np.asarray(mean / norm, dtype=np.float32)
        cache[key] = vector
        return vector

    def _similarity(
        self, source, source_start, source_size, target, target_start, target_size, cache
    ) -> float | None:
        source_vector = self._span_vector(source, source_start, source_size, cache)
        target_vector = self._span_vector(target, target_start, target_size, cache)
        if source_vector is None or target_vector is None:
            return None
        value = float(np.dot(source_vector, target_vector))
        return float(np.clip(value, -1.0, 1.0))

    def _gaps(self, spans: tuple[AlignmentSpan, ...], source: EmbeddedUnits, target: EmbeddedUnits) -> tuple[GapCandidate, ...]:
        groups: list[tuple[int, int]] = []
        index = 0
        while index < len(spans):
            span = spans[index]
            if span.source_unit_ids and span.target_unit_ids:
                index += 1
                continue
            end = index + 1
            while end < len(spans) and (bool(spans[end].source_unit_ids), bool(spans[end].target_unit_ids)) == (bool(span.source_unit_ids), bool(span.target_unit_ids)):
                end += 1
            groups.append((index, end))
            index = end
        candidates: list[GapCandidate] = []
        for start, end in groups:
            group = spans[start:end]
            side = "source" if group[0].source_unit_ids else "target"
            source_ids = tuple(unit_id for span in group for unit_id in span.source_unit_ids)
            target_ids = tuple(unit_id for span in group for unit_id in span.target_unit_ids)
            left = spans[start - 1] if start else None
            right = spans[end] if end < len(spans) else None
            left_ok = left if left and left.source_unit_ids and left.target_unit_ids and left.similarity >= self.config.anchor_similarity else None
            right_ok = right if right and right.source_unit_ids and right.target_unit_ids and right.similarity >= self.config.anchor_similarity else None
            repairable = side == "source" and left_ok is not None and right_ok is not None
            signals = ("missing_in_target",) if side == "source" else ("addition",)
            identity = "\x1f".join((source.document_id, target.document_id, side, *source_ids, "|", *target_ids))
            candidates.append(GapCandidate("gap-" + hashlib.sha256(identity.encode()).hexdigest()[:20], side, source_ids, target_ids, left_ok, right_ok, repairable, signals))
        return tuple(candidates)
