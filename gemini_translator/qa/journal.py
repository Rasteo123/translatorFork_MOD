"""Versioned JSON persistence for durable translation-QA history."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
import os
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from .models import ChapterMetrics, QaJournalEntry


class QaJournalError(ValueError):
    """Base error for journal data that cannot safely be used."""


class QaJournalCorruptedError(QaJournalError):
    """Raised for malformed or structurally invalid persisted JSON."""


class QaJournalUnsupportedVersionError(QaJournalError):
    """Raised when a journal uses a schema version this code cannot read."""


class QaJournal:
    SCHEMA_VERSION = 1

    def __init__(
        self,
        *,
        book_id: str,
        updated_at: str,
        metrics: Mapping[str, ChapterMetrics] | None = None,
        candidates: list[dict[str, Any]] | None = None,
        repairs: list[dict[str, Any]] | None = None,
        glossary_observations: list[dict[str, Any]] | None = None,
    ) -> None:
        self.book_id = book_id
        self.updated_at = updated_at
        self.metrics = dict(metrics or {})
        self.candidates = list(candidates or [])
        self.repairs = list(repairs or [])
        self.glossary_observations = list(glossary_observations or [])

    @classmethod
    def empty(cls, *, book_id: str) -> "QaJournal":
        return cls(book_id=book_id, updated_at=datetime.now().astimezone().isoformat())

    @classmethod
    def load(cls, path: Path) -> "QaJournal":
        try:
            with Path(path).open("r", encoding="utf-8") as stream:
                payload = json.load(stream)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise QaJournalCorruptedError(f"Cannot read QA journal: {path}") from exc

        if not isinstance(payload, dict):
            raise QaJournalCorruptedError("QA journal root must be an object")
        if "schema_version" not in payload or not isinstance(
            payload["schema_version"], int
        ):
            raise QaJournalCorruptedError("QA journal has no valid schema version")
        if payload["schema_version"] != cls.SCHEMA_VERSION:
            raise QaJournalUnsupportedVersionError(
                f"Unsupported QA journal schema version: {payload['schema_version']}"
            )

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
        except (KeyError, ValueError) as exc:
            raise QaJournalCorruptedError("QA journal metrics are invalid") from exc

        return cls(
            book_id=payload["book_id"],
            updated_at=payload["updated_at"],
            metrics=metrics,
            candidates=deepcopy(payload["candidates"]),
            repairs=deepcopy(payload["repairs"]),
            glossary_observations=deepcopy(payload["glossary_observations"]),
        )

    def append(self, entry: QaJournalEntry) -> None:
        self.candidates.append(entry.to_dict())
        self._mark_updated()

    def upsert_metrics(self, metrics: ChapterMetrics) -> None:
        self.metrics[metrics.chapter_id] = metrics
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
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.tmp")
        payload = self._payload()
        try:
            with temporary.open("w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        except OSError:
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
            "glossary_observations": deepcopy(self.glossary_observations),
        }

    def _mark_updated(self) -> None:
        self.updated_at = datetime.now().astimezone().isoformat()
