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

import hashlib

from ..disk_cache import DEFAULT_TTL_SECONDS, DiskTtlCache


__all__ = ("DEFAULT_TTL_SECONDS", "QaAnswerCache", "answer_digest")


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


class QaAnswerCache(DiskTtlCache):
    """Store answers under a root the user can delete at any time.

    The disk mechanics (TTL, atomic write, path layout, pruning an expired
    file on read) live in DiskTtlCache; this class only knows that the
    payload's one interesting field is called "answer".
    """

    def get(self, digest: str) -> object | None:
        """Return a stored answer, or nothing when it is missing, old, or broken."""
        payload = self.get_raw(digest)
        if payload is None:
            return None
        return payload.get("answer")

    def put(self, digest: str, answer: object) -> None:
        """Store one answer atomically; a failure here is never fatal."""
        self.put_raw(digest, {"answer": answer})
