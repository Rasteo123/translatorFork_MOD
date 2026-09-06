"""A disposable cache of rule answers, keyed by everything that can change one."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib

from ..disk_cache import DEFAULT_TTL_SECONDS, DiskTtlCache
from .base import LanguageRuleMatch


__all__ = (
    "DEFAULT_TTL_SECONDS",
    "LanguageRuleCache",
    "LanguageRuleCacheKey",
    "fingerprint_text",
)


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


class LanguageRuleCache(DiskTtlCache):
    """Store answers under a root the user can delete at any time.

    The disk mechanics (TTL, atomic write, path layout, pruning an expired
    file on read) live in DiskTtlCache; this class only knows how to turn a
    LanguageRuleCacheKey into a digest and a tuple of LanguageRuleMatch into
    (and back out of) JSON.
    """

    def get(self, key: LanguageRuleCacheKey) -> tuple[LanguageRuleMatch, ...] | None:
        """Return a stored answer, or nothing when it is missing or too old."""
        payload = self.get_raw(key.digest())
        if payload is None:
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
        self.put_raw(
            key.digest(),
            {
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
            },
        )
