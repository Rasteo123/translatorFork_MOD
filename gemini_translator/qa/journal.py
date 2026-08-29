"""Versioned JSON persistence for durable translation-QA history."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping

import pandas as pd

from .models import ChapterMetrics, GlossaryObservation, QaJournalEntry


class QaJournalError(ValueError):
    """Base error for journal data that cannot safely be used."""


class QaJournalCorruptedError(QaJournalError):
    """Raised for malformed or structurally invalid persisted JSON."""


class QaJournalUnsupportedVersionError(QaJournalError):
    """Raised when a journal uses a schema version this code cannot read."""


class QaJournal:
    SCHEMA_VERSION = 1
    _ROOT_KEYS = frozenset(
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

    def __init__(
        self,
        *,
        book_id: str,
        updated_at: str,
        metrics: Mapping[str, ChapterMetrics] | None = None,
        candidates: list[dict[str, Any]] | None = None,
        repairs: list[dict[str, Any]] | None = None,
        glossary_observations: Iterable[GlossaryObservation] | None = None,
    ) -> None:
        self.book_id = book_id
        self.updated_at = updated_at
        self.metrics = dict(metrics or {})
        self.candidates = list(candidates or [])
        self.repairs = list(repairs or [])
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
        if payload["schema_version"] != cls.SCHEMA_VERSION:
            raise QaJournalUnsupportedVersionError(
                f"Unsupported QA journal schema version: {payload['schema_version']}"
            )
        if set(payload) != cls._ROOT_KEYS:
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
        except (KeyError, ValueError) as exc:
            raise QaJournalCorruptedError("QA journal metrics are invalid") from exc

        return cls(
            book_id=payload["book_id"],
            updated_at=payload["updated_at"],
            metrics=metrics,
            candidates=candidates,
            repairs=repairs,
            glossary_observations=glossary_observations,
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
