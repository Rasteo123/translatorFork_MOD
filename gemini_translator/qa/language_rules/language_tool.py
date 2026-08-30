"""Direct HTTP access to a LanguageTool server the user configured themselves.

This deliberately does not use ``language_tool_python`` and never starts Java:
the application talks to one explicitly chosen address and nothing else.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from urllib.parse import urlsplit

from .base import (
    LanguageRuleMatch,
    LanguageRuleRequest,
    LanguageRuleUnavailable,
    batch_text,
    language_tool_code,
)


DEFAULT_TIMEOUT_SECONDS = 20.0
MAX_BATCH_CHARS = 20000


def normalize_endpoint(endpoint: str) -> str:
    """Return the check URL for an address the user typed, and nothing else."""
    text = str(endpoint or "").strip()
    if not text:
        raise LanguageRuleUnavailable("language_tool_endpoint_missing")
    parts = urlsplit(text)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise LanguageRuleUnavailable("language_tool_endpoint_invalid")
    path = parts.path.rstrip("/")
    if not path.endswith("/check"):
        path = f"{path}/check"
    return f"{parts.scheme}://{parts.netloc}{path}"


class LanguageToolHttpProvider:
    """Send one batch of sentences to one configured server, or fail closed."""

    name = "language_tool"

    def __init__(
        self,
        *,
        endpoint: str,
        session_factory,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if not callable(session_factory):
            raise TypeError("session_factory must be callable")
        self.endpoint = normalize_endpoint(endpoint)
        self._session_factory = session_factory
        self._timeout_seconds = _positive_timeout(timeout_seconds)
        self.server_version = ""

    async def check(
        self, request: LanguageRuleRequest
    ) -> tuple[LanguageRuleMatch, ...]:
        """Return every usable rule hit, mapped back onto the semantic units."""
        if not isinstance(request, LanguageRuleRequest):
            raise TypeError("request must be a LanguageRuleRequest")
        if not request.units:
            return ()
        text, spans = batch_text(request.units)
        if len(text) > MAX_BATCH_CHARS:
            raise LanguageRuleUnavailable("language_tool_batch_too_large")
        form = {
            "text": text,
            "language": language_tool_code(request.language),
        }
        if request.disabled_rule_ids:
            form["disabledRules"] = ",".join(request.disabled_rule_ids)

        payload = await self._post(form)
        software = payload.get("software")
        if isinstance(software, Mapping):
            self.server_version = str(software.get("version", "") or "")
        matches = payload.get("matches")
        if not isinstance(matches, list):
            raise LanguageRuleUnavailable("language_tool_invalid_response")
        return tuple(
            item
            for item in (_match_for(entry, spans, text) for entry in matches)
            if item is not None
        )

    async def _post(self, form: dict[str, str]) -> Mapping[str, object]:
        try:
            async with self._session_factory() as session:
                async with session.post(
                    self.endpoint, data=form, timeout=self._timeout_seconds
                ) as response:
                    status = getattr(response, "status", None)
                    if isinstance(status, bool) or not isinstance(status, int):
                        raise LanguageRuleUnavailable("language_tool_invalid_response")
                    if not 200 <= status < 300:
                        raise LanguageRuleUnavailable(f"language_tool_http_{status}")
                    payload = await response.json()
        except asyncio.CancelledError:
            raise
        except LanguageRuleUnavailable:
            raise
        except TimeoutError:
            raise LanguageRuleUnavailable("language_tool_timeout") from None
        except Exception:
            # The failure detail may carry the server's own output; only the
            # typed reason is safe to keep.
            raise LanguageRuleUnavailable("language_tool_unreachable") from None
        if not isinstance(payload, Mapping):
            raise LanguageRuleUnavailable("language_tool_invalid_response")
        return payload


def _match_for(entry: object, spans, text: str) -> LanguageRuleMatch | None:
    """Map one server match onto a single unit, or drop it as unusable."""
    if not isinstance(entry, Mapping):
        return None
    offset = entry.get("offset")
    length = entry.get("length")
    if (
        isinstance(offset, bool)
        or isinstance(length, bool)
        or not isinstance(offset, int)
        or not isinstance(length, int)
        or offset < 0
        or length <= 0
        or offset + length > len(text)
    ):
        return None
    rule = entry.get("rule")
    if not isinstance(rule, Mapping):
        return None
    rule_id = str(rule.get("id", "") or "").strip()
    if not rule_id:
        return None
    category = rule.get("category")
    category_id = (
        str(category.get("id", "") or "").strip()
        if isinstance(category, Mapping)
        else ""
    ) or "UNKNOWN"
    message = str(entry.get("message", "") or "").strip() or rule_id

    end = offset + length
    owner = next(
        (span for span in spans if span[0] <= offset and end <= span[1]), None
    )
    if owner is None:
        # A hit spanning two units or the separator between them cannot be
        # turned into a local edit; it is reported without a replacement.
        neighbour = next(
            (span for span in spans if span[0] < end and offset < span[1]), None
        )
        if neighbour is None:
            return None
        start, stop, unit = neighbour
        return LanguageRuleMatch(
            rule_id=rule_id,
            category=category_id,
            message=message,
            unit_id=unit.unit_id,
            block_id=unit.block_id,
            unit_start=max(offset - start, 0),
            unit_end=min(end - start, stop - start),
            matched_text=text[offset:end],
            replacements=(),
            report_only=True,
        )

    start, _stop, unit = owner
    replacements = tuple(
        str(item.get("value", ""))
        for item in (entry.get("replacements") or [])
        if isinstance(item, Mapping) and str(item.get("value", "")).strip()
    )
    return LanguageRuleMatch(
        rule_id=rule_id,
        category=category_id,
        message=message,
        unit_id=unit.unit_id,
        block_id=unit.block_id,
        unit_start=offset - start,
        unit_end=end - start,
        matched_text=text[offset:end],
        replacements=replacements,
        report_only=not replacements,
    )


def _positive_timeout(value: object) -> float:
    try:
        timeout = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_SECONDS
    if timeout <= 0 or timeout != timeout or timeout == float("inf"):
        return DEFAULT_TIMEOUT_SECONDS
    return timeout
