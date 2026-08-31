"""Remember what a model answered, keyed by the exact prompt that asked it.

A QA stage sends one deterministic prompt built from the chapter, the settings,
and the hints of the moment.  Ask the same question of the same model twice —
a deferred chapter retried after the network came back, a manual re-check, a
resumed session — and the second answer is bought at full price for nothing.

The question is the whole key on purpose: the data lines a stage builds carry
everything that could change an answer — the text, the settings, the hints — so
there is no list of key fields to keep in step with the prompt builder.  The
rendered prompt itself cannot be the key: it wraps the data in a boundary tag
that is deliberately random for every call, which is what keeps the data from
being read as instructions.

Only answers that passed their own validation are stored, and a cached answer is
validated again on the way out, so a damaged cache can slow a check down but
never talk it into anything.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import time


DEFAULT_TTL_SECONDS = 14 * 24 * 3600
_SAFE_NAME = re.compile(r"[^a-f0-9]+")


def answer_digest(
    question: str, prompt_version: str, model_provider: str, model_name: str
) -> str:
    """Identify one question: this data, under these instructions, to this model."""
    parts = (
        str(question or ""),
        str(prompt_version or ""),
        str(model_provider or ""),
        str(model_name or ""),
    )
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


class QaAnswerCache:
    """Store answers under a root the user can delete at any time."""

    def __init__(self, root: Path | str, ttl_seconds: float = DEFAULT_TTL_SECONDS) -> None:
        self.root = Path(root)
        self.ttl_seconds = float(ttl_seconds)

    def get(self, digest: str) -> object | None:
        """Return a stored answer, or nothing when it is missing, old, or broken."""
        try:
            payload = json.loads(self._path(digest).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(payload, dict):
            return None
        stored_at = payload.get("stored_at")
        if not isinstance(stored_at, (int, float)):
            return None
        if self.ttl_seconds > 0 and time.time() - stored_at > self.ttl_seconds:
            return None
        return payload.get("answer")

    def put(self, digest: str, answer: object) -> None:
        """Store one answer atomically; a failure here is never fatal."""
        try:
            body = json.dumps(
                {
                    "stored_at": time.time(),
                    "created_at": datetime.now(timezone.utc).isoformat(
                        timespec="seconds"
                    ),
                    "answer": answer,
                },
                ensure_ascii=False,
            )
        except (TypeError, ValueError):
            return
        path = self._path(digest)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(path.name + ".tmp")
            temporary.write_text(body, encoding="utf-8")
            os.replace(temporary, path)
        except OSError:
            return

    def _path(self, digest: str) -> Path:
        safe = _SAFE_NAME.sub("", str(digest or ""))
        if len(safe) < 8:
            raise ValueError("answer digest must be a sha256 hex string")
        return self.root / safe[:2] / f"{safe}.json"
