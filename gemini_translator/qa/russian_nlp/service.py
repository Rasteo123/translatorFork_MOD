"""Decide whether local Russian NLP runs at all, and report why when it does not."""

from __future__ import annotations

from collections.abc import Sequence

from ..capabilities import QaCapabilitySettings
from ..models import SemanticUnit
from .base import RussianNlpResult, RussianNlpUnavailable


class RussianNlpService:
    """Create the provider lazily, and only for a capability that is switched on."""

    def __init__(self, provider_factory=None) -> None:
        self._provider_factory = provider_factory
        self._provider = None
        self._failed_reason = ""

    def analyze(
        self,
        units: Sequence[SemanticUnit],
        capabilities: QaCapabilitySettings,
    ) -> RussianNlpResult:
        """Return local evidence, or say plainly why there is none."""
        if not getattr(capabilities, "slovnet_enabled", False):
            return RussianNlpResult("disabled")
        if self._failed_reason:
            return RussianNlpResult("unavailable", warnings=(self._failed_reason,))
        if self._provider is None:
            if not callable(self._provider_factory):
                return RussianNlpResult(
                    "unavailable", warnings=("slovnet_not_configured",)
                )
            try:
                self._provider = self._provider_factory()
            except RussianNlpUnavailable as error:
                self._failed_reason = error.reason
                return RussianNlpResult("unavailable", warnings=(error.reason,))
            except Exception:  # noqa: BLE001 - a broken install is an outage
                self._failed_reason = "slovnet_unavailable"
                return RussianNlpResult("unavailable", warnings=("slovnet_unavailable",))
        if self._provider is None:
            return RussianNlpResult("unavailable", warnings=("slovnet_unavailable",))

        try:
            report = self._provider.analyze(tuple(units or ()))
        except RussianNlpUnavailable as error:
            return RussianNlpResult("unavailable", warnings=(error.reason,))
        except Exception:  # noqa: BLE001 - a model defect must not break a chapter
            return RussianNlpResult("unavailable", warnings=("slovnet_unavailable",))
        return RussianNlpResult("completed", report)
