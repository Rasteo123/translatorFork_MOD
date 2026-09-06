"""A disposable, self-pruning JSON cache on disk, keyed by a hex digest.

QA runs cache several different kinds of answers this way: an LLM's raw reply
to a deterministic prompt, a rule-checker's verdict on one unit of text. Each
of those caches differs only in what it stores under ``"answer"``-shaped
fields and how it turns its own domain key into a digest — the storage
mechanics (TTL, atomic write, path layout) are exactly the same, so they live
here once.

A stale entry is deleted the moment it is found stale, not merely ignored:
``put()`` never revisits old files, so a cache that only ever skipped expired
entries would grow without bound (qa-b/bugs/3-qa-disk-caches-never-pruned).
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import time


DEFAULT_TTL_SECONDS = 14 * 24 * 3600
_SAFE_NAME = re.compile(r"[^a-f0-9]+")


class DiskTtlCache:
    """Store JSON payloads under a root the user can delete at any time."""

    def __init__(self, root: Path | str, ttl_seconds: float = DEFAULT_TTL_SECONDS) -> None:
        self.root = Path(root)
        self.ttl_seconds = float(ttl_seconds)

    def get_raw(self, digest: str) -> dict | None:
        """Return the stored payload, or nothing when it is missing, old, or broken."""
        path = self._path(digest)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(payload, dict):
            return None
        stored_at = payload.get("stored_at")
        if not isinstance(stored_at, (int, float)):
            return None
        if self.ttl_seconds > 0 and time.time() - stored_at > self.ttl_seconds:
            # A stale entry must not survive its TTL on disk physically, or the
            # cache directory grows without bound: put() only ever adds files
            # (qa-b/bugs/3-qa-disk-caches-never-pruned).
            try:
                path.unlink()
            except OSError:
                pass
            return None
        return payload

    def put_raw(self, digest: str, fields: dict) -> None:
        """Store one payload atomically; a failure here is never fatal."""
        try:
            body = json.dumps(
                {
                    "stored_at": time.time(),
                    "created_at": datetime.now(timezone.utc).isoformat(
                        timespec="seconds"
                    ),
                    **fields,
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
            raise ValueError("cache digest must be a sha256 hex string")
        return self.root / safe[:2] / f"{safe}.json"
