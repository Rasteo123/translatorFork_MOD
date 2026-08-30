"""Versioned JSON persistence for durable translation-QA history."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping

import pandas as pd

from .models import (
    ChapterMetrics,
    GlossaryObservation,
    QaChapterState,
    QaJournalEntry,
    QaModelValidationError,
)


class QaJournalError(ValueError):
    """Base error for journal data that cannot safely be used."""


class QaJournalCorruptedError(QaJournalError):
    """Raised for malformed or structurally invalid persisted JSON."""


class QaJournalUnsupportedVersionError(QaJournalError):
    """Raised when a journal uses a schema version this code cannot read."""


class QaJournal:
    SCHEMA_VERSION = 2
    _V1_ROOT_KEYS = frozenset(
        {
            "schema_version",
            "book_id",
            "updated_at",
            "metrics",
            "candidates",
            "repairs",
            "glossary_observations",
        }
    )
    # v2 adds the per-chapter check state the final book pass selects on.
    _ROOT_KEYS = _V1_ROOT_KEYS | {"chapter_states"}
    _ROOT_KEYS_BY_VERSION = {1: _V1_ROOT_KEYS, 2: _ROOT_KEYS}

    def __init__(
        self,
        *,
        book_id: str,
        updated_at: str,
        metrics: Mapping[str, ChapterMetrics] | None = None,
        candidates: list[dict[str, Any]] | None = None,
        repairs: list[dict[str, Any]] | None = None,
        glossary_observations: Iterable[GlossaryObservation] | None = None,
        chapter_states: Mapping[str, QaChapterState] | None = None,
    ) -> None:
        self.book_id = book_id
        self.updated_at = updated_at
        self.metrics = dict(metrics or {})
        self.candidates = list(candidates or [])
        self.repairs = list(repairs or [])
        self.chapter_states = dict(chapter_states or {})
        self.glossary_observations = list(glossary_observations or [])
        if any(
            not isinstance(observation, GlossaryObservation)
            for observation in self.glossary_observations
        ):
            raise QaJournalError("glossary observations must use the typed schema")

    @classmethod
    def empty(cls, *, book_id: str) -> "QaJournal":
        return cls(book_id=book_id, updated_at=datetime.now().astimezone().isoformat())

    @classmethod
    def load(cls, path: Path) -> "QaJournal":
        try:
            with Path(path).open("r", encoding="utf-8") as stream:
                payload = json.load(stream, parse_constant=_reject_json_constant)
        except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
            raise QaJournalCorruptedError(f"Cannot read QA journal: {path}") from exc

        if not isinstance(payload, dict):
            raise QaJournalCorruptedError("QA journal root must be an object")
        if "schema_version" not in payload or isinstance(
            payload["schema_version"], bool
        ) or not isinstance(
            payload["schema_version"], int
        ):
            raise QaJournalCorruptedError("QA journal has no valid schema version")
        version = payload["schema_version"]
        expected_keys = cls._ROOT_KEYS_BY_VERSION.get(version)
        if expected_keys is None:
            raise QaJournalUnsupportedVersionError(
                f"Unsupported QA journal schema version: {version}"
            )
        if set(payload) != expected_keys:
            raise QaJournalCorruptedError("QA journal has an invalid root schema")

        required_lists = ("metrics", "candidates", "repairs", "glossary_observations")
        if not isinstance(payload.get("book_id"), str) or not isinstance(
            payload.get("updated_at"), str
        ):
            raise QaJournalCorruptedError("QA journal is missing book metadata")
        if any(not isinstance(payload.get(name), list) for name in required_lists):
            raise QaJournalCorruptedError("QA journal contains invalid collections")

        try:
            metrics = {
                item["chapter_id"]: ChapterMetrics.from_dict(item)
                for item in payload["metrics"]
                if isinstance(item, dict)
            }
            if len(metrics) != len(payload["metrics"]):
                raise ValueError("metrics entries must be objects")
            candidates = _validated_object_entries(payload["candidates"])
            repairs = _validated_object_entries(payload["repairs"])
            glossary_observations = [
                GlossaryObservation.from_dict(item)
                for item in payload["glossary_observations"]
            ]
            # A v1 journal simply has no recorded states: every chapter is then
            # treated as never checked by the final pass, which is the safe side.
            states = [
                QaChapterState.from_dict(item)
                for item in payload.get("chapter_states", [])
            ]
        except (KeyError, ValueError, QaModelValidationError) as exc:
            raise QaJournalCorruptedError("QA journal metrics are invalid") from exc

        return cls(
            book_id=payload["book_id"],
            updated_at=payload["updated_at"],
            metrics=metrics,
            candidates=candidates,
            repairs=repairs,
            glossary_observations=glossary_observations,
            chapter_states={state.chapter_id: state for state in states},
        )

    def append(self, entry: QaJournalEntry) -> None:
        self.candidates.append(entry.to_dict())
        self._mark_updated()

    def upsert_metrics(self, metrics: ChapterMetrics) -> None:
        self.metrics[metrics.chapter_id] = metrics
        self._mark_updated()

    def append_glossary_observation(self, observation: GlossaryObservation) -> None:
        if not isinstance(observation, GlossaryObservation):
            raise QaJournalError("glossary observation must use the typed schema")
        self.glossary_observations.append(observation)
        self._mark_updated()

    def append_repair(self, record: Mapping[str, Any]) -> None:
        """Record one applied repair so a restart never repeats it."""
        if not isinstance(record, Mapping):
            raise QaJournalError("repair record must be a mapping")
        entry = {str(key): value for key, value in record.items()}
        if not isinstance(entry.get("patch_id"), str) or not entry["patch_id"].strip():
            raise QaJournalError("repair record needs a patch_id")
        if any(item.get("patch_id") == entry["patch_id"] for item in self.repairs):
            return
        self.repairs.append(entry)
        self._mark_updated()

    def record_chapter_state(self, state: QaChapterState) -> None:
        """Remember how and under what rules one chapter was last checked."""
        if not isinstance(state, QaChapterState):
            raise QaJournalError("chapter state must use the typed schema")
        self.chapter_states[state.chapter_id] = state
        self._mark_updated()

    def record_chapter_result(
        self,
        *,
        metrics: ChapterMetrics | None = None,
        entries: Iterable[QaJournalEntry] = (),
        repairs: Iterable[Mapping[str, Any]] = (),
        state: QaChapterState | None = None,
    ) -> None:
        """Fold one chapter QA pass into the journal in a single step."""
        if metrics is not None:
            self.upsert_metrics(metrics)
        if state is not None:
            self.record_chapter_state(state)
        for entry in entries:
            if not isinstance(entry, QaJournalEntry):
                raise QaJournalError("journal entries must use the typed schema")
            self.append(entry)
        for repair in repairs:
            self.append_repair(repair)

    def metrics_frame(self) -> pd.DataFrame:
        rows = [
            self.metrics[chapter_id].to_dict() for chapter_id in sorted(self.metrics)
        ]
        return pd.DataFrame(rows, columns=ChapterMetrics.dataframe_columns())

    def to_frame(self) -> pd.DataFrame:
        return self.metrics_frame()

    def save(self, path: Path) -> None:
        target = Path(path)
        temporary = target.with_name(f".{target.name}.tmp")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            payload = self._payload()
            with temporary.open("w", encoding="utf-8") as stream:
                json.dump(
                    payload,
                    stream,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                    allow_nan=False,
                )
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        except BaseException:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    def _payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "book_id": self.book_id,
            "updated_at": self.updated_at,
            "metrics": [
                self.metrics[chapter_id].to_dict() for chapter_id in sorted(self.metrics)
            ],
            "candidates": deepcopy(self.candidates),
            "chapter_states": [
                self.chapter_states[chapter_id].to_dict()
                for chapter_id in sorted(self.chapter_states)
            ],
            "repairs": deepcopy(self.repairs),
            "glossary_observations": [
                observation.to_dict() for observation in self.glossary_observations
            ],
        }

    def _mark_updated(self) -> None:
        self.updated_at = datetime.now().astimezone().isoformat()


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Unsupported JSON numeric constant: {value}")


def _validated_object_entries(entries: list[Any]) -> list[dict[str, Any]]:
    validated: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("QA journal collection entries must be objects")
        if set(entry) == {"entry_id", "chapter_id", "decision"}:
            validated.append(QaJournalEntry.from_dict(entry).to_dict())
        else:
            validated.append(deepcopy(entry))
    return validated
