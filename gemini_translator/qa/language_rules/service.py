"""One place that decides whether rule checking runs at all, and reports why."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from ..capabilities import QaCapabilitySettings
from ..models import SemanticUnit
from .base import (
    LanguageRuleRequest,
    LanguageRuleResult,
    LanguageRuleUnavailable,
    batch_text,
)
from .cache import LanguageRuleCacheKey, fingerprint_text


class LanguageRuleService:
    """Run the configured rule provider only when its capability is on.

    A disabled capability creates no provider and makes no request. An
    unavailable one produces a warning and an empty result, so the translation
    session continues exactly as it would without the analyzer.
    """

    def __init__(
        self,
        *,
        provider=None,
        cache=None,
        language: str = "ru",
        disabled_rule_ids: Sequence[str] = (),
        preprocessing_version: str = "",
    ) -> None:
        self._provider = provider
        self._cache = cache
        self._language = str(language or "ru")
        self._disabled_rule_ids = tuple(disabled_rule_ids or ())
        self._preprocessing_version = str(preprocessing_version or "")

    async def collect(
        self,
        units: Sequence[SemanticUnit],
        capabilities: QaCapabilitySettings,
    ) -> LanguageRuleResult:
        """Return rule hits for one chapter, or say why there are none."""
        if not getattr(capabilities, "language_tool_enabled", False):
            return LanguageRuleResult("disabled")
        if self._provider is None:
            return LanguageRuleResult(
                "unavailable", warnings=("language_tool_not_configured",)
            )
        units = tuple(units or ())
        if not units:
            return LanguageRuleResult("completed")

        request = LanguageRuleRequest(
            units=units,
            language=self._language,
            disabled_rule_ids=self._disabled_rule_ids,
        )
        key = self._cache_key(request)
        if self._cache is not None and key is not None:
            cached = self._cache.get(key)
            if cached is not None:
                return LanguageRuleResult("completed", tuple(cached))

        try:
            issues = await self._provider.check(request)
        except asyncio.CancelledError:
            raise
        except LanguageRuleUnavailable as error:
            return LanguageRuleResult("unavailable", warnings=(error.reason,))
        except Exception:  # noqa: BLE001 - any provider defect is an outage here
            return LanguageRuleResult(
                "unavailable", warnings=("language_tool_unreachable",)
            )

        issues = tuple(issues)
        if self._cache is not None:
            # The key is recomputed: only after the answer do we know the
            # server version it belongs to.
            fresh_key = self._cache_key(request)
            if fresh_key is not None:
                self._cache.put(fresh_key, issues)
        return LanguageRuleResult("completed", issues)

    def _cache_key(self, request: LanguageRuleRequest) -> LanguageRuleCacheKey | None:
        endpoint = str(getattr(self._provider, "endpoint", "") or "")
        if not endpoint:
            return None
        text, _spans = batch_text(request.units)
        return LanguageRuleCacheKey(
            text_fingerprint=fingerprint_text(text),
            language=request.language,
            endpoint=endpoint,
            server_version=str(getattr(self._provider, "server_version", "") or ""),
            disabled_rule_ids=request.disabled_rule_ids,
            preprocessing_version=self._preprocessing_version,
        )
