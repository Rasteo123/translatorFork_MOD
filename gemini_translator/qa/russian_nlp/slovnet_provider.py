"""Turn Slovnet/Navec output into this project's own evidence types.

Nothing here imports slovnet or navec at module load: an installed-and-enabled
analyzer is the only thing that ever touches those packages.
"""

from __future__ import annotations

from collections.abc import Sequence
import re

from .._common import bounded_int as _bounded
from ..models import SemanticUnit
from .base import (
    ENTITY_TYPES,
    MorphologyCandidate,
    ProtectedEntity,
    RussianNlpReport,
    RussianNlpUnavailable,
    SyntaxCandidate,
)


MAX_CPU_THREADS = 16
MAX_BATCH_SIZE = 128
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)


def tokenize(text: str) -> tuple[tuple[str, int, int], ...]:
    """Split one unit into tokens with the offsets the model reports against."""
    return tuple(
        (match.group(0), match.start(), match.end())
        for match in _TOKEN_RE.finditer(str(text or ""))
    )


class SlovnetProvider:
    """Read one runtime's answers and hand back typed, non-actionable evidence."""

    name = "slovnet"

    def __init__(
        self,
        runtime,
        *,
        cpu_threads: int = 2,
        batch_size: int = 16,
        model_versions=None,
    ) -> None:
        if runtime is None:
            raise RussianNlpUnavailable("slovnet_runtime_missing")
        self._runtime = runtime
        self.cpu_threads = _bounded(cpu_threads, minimum=1, maximum=MAX_CPU_THREADS, default=2)
        self.batch_size = _bounded(batch_size, minimum=1, maximum=MAX_BATCH_SIZE, default=16)
        self._model_versions = dict(model_versions or {})

    def analyze(self, units: Sequence[SemanticUnit]) -> RussianNlpReport:
        """Return entities and unconfirmed candidates for one chapter."""
        entities: list[ProtectedEntity] = []
        morphology: list[MorphologyCandidate] = []
        syntax: list[SyntaxCandidate] = []
        for unit in units or ():
            tokens = tokenize(unit.text)
            entities.extend(self._entities(unit))
            morphology.extend(self._morphology(unit, tokens))
            syntax.extend(self._syntax(unit, tokens))
        return RussianNlpReport(
            protected_entities=tuple(entities),
            morphology_candidates=tuple(morphology),
            syntax_candidates=tuple(syntax),
            model_versions=dict(self._model_versions),
        )

    def _entities(self, unit: SemanticUnit):
        for span in _call(self._runtime, "ner_spans", unit.text):
            start, end, entity_type = _span_fields(span)
            if entity_type not in ENTITY_TYPES:
                continue
            if start is None or end is None or not 0 <= start < end <= len(unit.text):
                continue
            yield ProtectedEntity(
                unit_id=unit.unit_id,
                block_id=unit.block_id,
                start=start,
                end=end,
                entity_type=entity_type,
                text=unit.text[start:end],
            )

    def _morphology(self, unit: SemanticUnit, tokens):
        for item in _call(self._runtime, "morph_candidates", tokens):
            index = _int(item.get("token_id"))
            category = str(item.get("category", "") or "").strip()
            if index is None or not category or not 0 <= index < len(tokens):
                continue
            _token, start, end = tokens[index]
            yield MorphologyCandidate(
                unit_id=unit.unit_id,
                block_id=unit.block_id,
                start=start,
                end=end,
                category=category,
                confidence=_confidence(item.get("confidence")),
            )

    def _syntax(self, unit: SemanticUnit, tokens):
        for item in _call(self._runtime, "syntax_candidates", tokens):
            token_ids = tuple(
                index
                for index in (_int(value) for value in item.get("token_ids", ()))
                if index is not None and 0 <= index < len(tokens)
            )
            category = str(item.get("category", "") or "").strip()
            if not token_ids or not category:
                continue
            start = min(tokens[index][1] for index in token_ids)
            end = max(tokens[index][2] for index in token_ids)
            yield SyntaxCandidate(
                unit_id=unit.unit_id,
                block_id=unit.block_id,
                token_ids=token_ids,
                category=category,
                text=unit.text[start:end],
                confidence=_confidence(item.get("confidence")),
            )


def _call(runtime, method: str, argument):
    handler = getattr(runtime, method, None)
    if not callable(handler):
        return ()
    try:
        return tuple(handler(argument) or ())
    except Exception:  # noqa: BLE001 - a model defect is missing evidence, not a crash
        return ()


def _span_fields(span):
    if isinstance(span, dict):
        return _int(span.get("start")), _int(span.get("stop", span.get("end"))), str(
            span.get("type", "") or ""
        )
    start = _int(getattr(span, "start", None))
    end = _int(getattr(span, "stop", getattr(span, "end", None)))
    return start, end, str(getattr(span, "type", "") or "")


def _confidence(value) -> str:
    text = str(value or "").strip().lower()
    return text if text in {"high", "medium", "ambiguous"} else "ambiguous"


def _int(value):
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def load_runtime(model_dir, *, cpu_threads: int = 2):
    """Load the real Slovnet runtime, importing its packages only here."""
    from pathlib import Path

    directory = Path(model_dir)
    try:
        from navec import Navec
        from slovnet import NER, Morph, Syntax
    except ImportError as error:
        raise RussianNlpUnavailable("slovnet_packages_missing") from error
    try:
        navec = Navec.load(str(directory / "navec_news_v1_1B_250K_300d_100q.tar"))
        ner = NER.load(str(directory / "slovnet_ner_news_v1.tar")).navec(navec)
        morph = Morph.load(
            str(directory / "slovnet_morph_news_v1.tar"),
            batch_size=_bounded(cpu_threads, minimum=1, maximum=MAX_CPU_THREADS, default=2),
        ).navec(navec)
        syntax = Syntax.load(str(directory / "slovnet_syntax_news_v1.tar")).navec(navec)
    except Exception as error:  # noqa: BLE001 - a bad install is an outage, not a crash
        raise RussianNlpUnavailable("slovnet_weights_unusable") from error
    return _SlovnetRuntime(ner, morph, syntax)


class _SlovnetRuntime:
    """Adapt the library's own objects to the narrow interface used above."""

    def __init__(self, ner, morph, syntax) -> None:
        self._ner = ner
        self._morph = morph
        self._syntax = syntax

    def ner_spans(self, text: str):
        markup = self._ner(text)
        return tuple(getattr(markup, "spans", ()) or ())

    def morph_candidates(self, tokens):
        words = [token for token, _start, _end in tokens]
        if not words:
            return ()
        markup = next(iter(self._morph.map([words])), None)
        candidates = []
        for index, token in enumerate(getattr(markup, "tokens", ()) or ()):
            feats = getattr(token, "feats", None) or {}
            if isinstance(feats, dict) and feats.get("Case") == "Nom" and index:
                candidates.append(
                    {"token_id": index, "category": "case_agreement", "confidence": "ambiguous"}
                )
        return tuple(candidates)

    def syntax_candidates(self, tokens):
        words = [token for token, _start, _end in tokens]
        if not words:
            return ()
        markup = next(iter(self._syntax.map([words])), None)
        candidates = []
        for index, token in enumerate(getattr(markup, "tokens", ()) or ()):
            relation = str(getattr(token, "rel", "") or "")
            if relation in {"orphan", "dep"}:
                candidates.append(
                    {
                        "token_ids": (index,),
                        "category": f"unusual_syntax:{relation}",
                        "confidence": "ambiguous",
                    }
                )
        return tuple(candidates)
