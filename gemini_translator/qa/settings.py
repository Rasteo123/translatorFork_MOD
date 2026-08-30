"""Persisted, Qt-free translation QA settings and their safe defaults."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace

from .capabilities import QaCapabilityKey, QaCapabilitySettings
from .service import QaOptions


SETTINGS_KEY = "translation_qa"
CORRECTION_MODEL_MODES = frozenset({"translation_model", "custom"})
LANGUAGE_TOOL_MODES = frozenset({"remote", "local"})
COMETKIWI_DEVICES = frozenset({"cpu", "cuda"})
EMBEDDING_PROVIDERS = frozenset({"auto", "gemini", "openai_compatible", "local_onnx"})


@dataclass(frozen=True, slots=True)
class QaSettings:
    """Everything the user can decide about translation QA, with safe defaults.

    Defaults keep the four chapter-level behaviours on and every heavyweight or
    outbound analyzer off. A capability flag alone never installs a model, runs
    Java, or sends text anywhere: each one also needs its own explicit setup,
    checked by :meth:`unsatisfied_requirements`.
    """

    check_completeness_after_chapter: bool = True
    auto_repair_confirmed_omissions: bool = True
    check_language_after_chapter: bool = True
    auto_repair_objective_language_issues: bool = True
    embedding_provider: str = "auto"
    embedding_model: str = ""
    correction_model_mode: str = "translation_model"
    correction_provider: str = ""
    correction_model: str = ""
    final_book_pass: bool = True
    capabilities: QaCapabilitySettings = field(default_factory=QaCapabilitySettings)
    language_tool_endpoint: str = ""
    language_tool_mode: str = "remote"
    language_tool_disabled_rules: tuple[str, ...] = ()
    slovnet_cpu_threads: int = 2
    slovnet_batch_size: int = 16
    cometkiwi_runner_path: str = ""
    cometkiwi_model: str = ""
    cometkiwi_device: str = "cpu"
    cometkiwi_license_accepted: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.capabilities, QaCapabilitySettings):
            object.__setattr__(self, "capabilities", QaCapabilitySettings())
        object.__setattr__(
            self,
            "embedding_provider",
            _choice(self.embedding_provider, EMBEDDING_PROVIDERS, "auto"),
        )
        object.__setattr__(
            self,
            "correction_model_mode",
            _choice(self.correction_model_mode, CORRECTION_MODEL_MODES, "translation_model"),
        )
        object.__setattr__(
            self,
            "language_tool_mode",
            _choice(self.language_tool_mode, LANGUAGE_TOOL_MODES, "remote"),
        )
        object.__setattr__(
            self,
            "cometkiwi_device",
            _choice(self.cometkiwi_device, COMETKIWI_DEVICES, "cpu"),
        )
        object.__setattr__(
            self,
            "language_tool_disabled_rules",
            tuple(
                dict.fromkeys(
                    str(rule).strip()
                    for rule in self.language_tool_disabled_rules or ()
                    if str(rule).strip()
                )
            ),
        )
        object.__setattr__(
            self, "slovnet_cpu_threads", _bounded_int(self.slovnet_cpu_threads, 2, 1, 32)
        )
        object.__setattr__(
            self, "slovnet_batch_size", _bounded_int(self.slovnet_batch_size, 16, 1, 512)
        )
        for field_name in (
            "embedding_model",
            "correction_provider",
            "correction_model",
            "language_tool_endpoint",
            "cometkiwi_runner_path",
            "cometkiwi_model",
        ):
            object.__setattr__(self, field_name, str(getattr(self, field_name) or "").strip())
        for field_name in (
            "check_completeness_after_chapter",
            "auto_repair_confirmed_omissions",
            "check_language_after_chapter",
            "auto_repair_objective_language_issues",
            "final_book_pass",
            "cometkiwi_license_accepted",
        ):
            object.__setattr__(self, field_name, bool(getattr(self, field_name)))

    @classmethod
    def from_dict(cls, payload: object) -> "QaSettings":
        """Rebuild settings from persisted JSON, ignoring anything unusable."""
        if not isinstance(payload, Mapping):
            return cls()
        known = {
            key: payload[key]
            for key in cls.__dataclass_fields__
            if key in payload and key != "capabilities"
        }
        capabilities = payload.get("capabilities")
        if isinstance(capabilities, Mapping):
            known["capabilities"] = QaCapabilitySettings(
                razdel_enabled=bool(capabilities.get("razdel_enabled", True)),
                language_tool_enabled=bool(
                    capabilities.get("language_tool_enabled", False)
                ),
                slovnet_enabled=bool(capabilities.get("slovnet_enabled", False)),
                cometkiwi_enabled=bool(capabilities.get("cometkiwi_enabled", False)),
            )
        try:
            return cls(**known)
        except TypeError:
            return cls()

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable snapshot of every setting."""
        return {
            "check_completeness_after_chapter": self.check_completeness_after_chapter,
            "auto_repair_confirmed_omissions": self.auto_repair_confirmed_omissions,
            "check_language_after_chapter": self.check_language_after_chapter,
            "auto_repair_objective_language_issues": (
                self.auto_repair_objective_language_issues
            ),
            "embedding_provider": self.embedding_provider,
            "embedding_model": self.embedding_model,
            "correction_model_mode": self.correction_model_mode,
            "correction_provider": self.correction_provider,
            "correction_model": self.correction_model,
            "final_book_pass": self.final_book_pass,
            "capabilities": {
                "razdel_enabled": self.capabilities.razdel_enabled,
                "language_tool_enabled": self.capabilities.language_tool_enabled,
                "slovnet_enabled": self.capabilities.slovnet_enabled,
                "cometkiwi_enabled": self.capabilities.cometkiwi_enabled,
            },
            "language_tool_endpoint": self.language_tool_endpoint,
            "language_tool_mode": self.language_tool_mode,
            "language_tool_disabled_rules": list(self.language_tool_disabled_rules),
            "slovnet_cpu_threads": self.slovnet_cpu_threads,
            "slovnet_batch_size": self.slovnet_batch_size,
            "cometkiwi_runner_path": self.cometkiwi_runner_path,
            "cometkiwi_model": self.cometkiwi_model,
            "cometkiwi_device": self.cometkiwi_device,
            "cometkiwi_license_accepted": self.cometkiwi_license_accepted,
        }

    def unsatisfied_requirements(self) -> tuple[str, ...]:
        """Return the capabilities that are switched on but not yet set up."""
        missing: list[str] = []
        if self.capabilities.language_tool_enabled and not self.language_tool_endpoint:
            missing.append(QaCapabilityKey.LANGUAGE_TOOL.value)
        if self.capabilities.cometkiwi_enabled and (
            not self.cometkiwi_runner_path or not self.cometkiwi_license_accepted
        ):
            missing.append(QaCapabilityKey.COMETKIWI.value)
        return tuple(missing)

    def effective_capabilities(self) -> QaCapabilitySettings:
        """Return capabilities with unconfigured ones switched off."""
        missing = set(self.unsatisfied_requirements())
        return replace(
            self.capabilities,
            language_tool_enabled=(
                self.capabilities.language_tool_enabled
                and QaCapabilityKey.LANGUAGE_TOOL.value not in missing
            ),
            cometkiwi_enabled=(
                self.capabilities.cometkiwi_enabled
                and QaCapabilityKey.COMETKIWI.value not in missing
            ),
        )

    def to_options(self) -> QaOptions:
        """Project the user's settings onto one QA pass configuration."""
        return QaOptions(
            capabilities=self.effective_capabilities(),
            check_completeness=self.check_completeness_after_chapter,
            auto_repair_omissions=self.auto_repair_confirmed_omissions,
            check_language=self.check_language_after_chapter,
            auto_repair_language=self.auto_repair_objective_language_issues,
        )

    def correction_model_for(
        self, translation_provider: str, translation_model: str
    ) -> tuple[str, str]:
        """Return the provider and model that must perform QA corrections."""
        if (
            self.correction_model_mode == "custom"
            and self.correction_provider
            and self.correction_model
        ):
            return self.correction_provider, self.correction_model
        return str(translation_provider or ""), str(translation_model or "")


def _choice(value: object, allowed: frozenset[str], default: str) -> str:
    text = str(value or "").strip()
    return text if text in allowed else default


def _bounded_int(value: object, default: int, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return min(max(value, minimum), maximum)
