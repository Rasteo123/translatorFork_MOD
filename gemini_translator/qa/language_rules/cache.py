"""A disposable cache of rule answers, keyed by everything that can change one."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import time

from .base import LanguageRuleMatch


DEFAULT_TTL_SECONDS = 14 * 24 * 3600
_SAFE_NAME = re.compile(r"[^a-f0-9]+")


@dataclass(frozen=True, slots=True)
class LanguageRuleCacheKey:
    """Everything that makes one rule answer valid, and nothing else.

    A different server version, a different disabled-rule list, or different
    segmentation all produce a different answer, so each takes part in the key.
    """

    text_fingerprint: str
    language: str
    endpoint: str
    server_version: str
    disabled_rule_ids: tuple[str, ...] = ()
    preprocessing_version: str = ""

    def digest(self) -> str:
        parts = (
            self.text_fingerprint,
            self.language,
            self.endpoint,
            self.server_version,
            "|".join(sorted(self.disabled_rule_ids)),
            self.preprocessing_version,
        )
        return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def fingerprint_text(text: str) -> str:
    """Return a stable fingerprint of the exact text that was checked."""
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


class LanguageRuleCache:
    """Store answers under a root the user can delete at any time."""

    def __init__(self, root: Path | str, ttl_seconds: float = DEFAULT_TTL_SECONDS) -> None:
        self.root = Path(root)
        self.ttl_seconds = float(ttl_seconds)

    def get(self, key: LanguageRuleCacheKey) -> tuple[LanguageRuleMatch, ...] | None:
        """Return a stored answer, or nothing when it is missing or too old."""
        path = self._path(key)
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
            return None
        entries = payload.get("issues")
        if not isinstance(entries, list):
            return None
        try:
            return tuple(LanguageRuleMatch(**entry) for entry in entries)
        except (TypeError, ValueError):
            return None

    def put(self, key: LanguageRuleCacheKey, issues) -> None:
        """Store one answer atomically; a failure here is never fatal."""
        payload = {
            "stored_at": time.time(),
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "issues": [
                {
                    "rule_id": issue.rule_id,
                    "category": issue.category,
                    "message": issue.message,
                    "unit_id": issue.unit_id,
                    "block_id": issue.block_id,
                    "unit_start": issue.unit_start,
                    "unit_end": issue.unit_end,
                    "matched_text": issue.matched_text,
                    "replacements": list(issue.replacements),
                    "report_only": issue.report_only,
                }
                for issue in issues
            ],
        }
        path = self._path(key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(path.name + ".tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
            os.replace(temporary, path)
        except OSError:
            return

    def _path(self, key: LanguageRuleCacheKey) -> Path:
        digest = _SAFE_NAME.sub("", key.digest())
        return self.root / digest[:2] / f"{digest}.json"
