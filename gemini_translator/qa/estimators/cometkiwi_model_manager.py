"""Install COMETKiwi weights only after the user has read what they agree to.

The weights are large, licensed, and useless to anyone who did not ask for
them, so nothing here happens on its own: enabling the capability installs
nothing, and ``install`` refuses until the caller states that the licence was
accepted.  Verification, atomic replacement, and uninstall come from the shared
bundle manager, which never touches anything above its own model directory.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ..model_bundle import (
    MANIFEST_NAME,
    ModelBundle,
    ModelBundleFile,
    ModelBundleManager,
)


CometKiwiModelFile = ModelBundleFile

__all__ = (
    "MANIFEST_NAME",
    "describe_cometkiwi_setup",
    "CometKiwiLicenseNotAccepted",
    "CometKiwiModelFile",
    "CometKiwiModelManager",
    "CometKiwiModelManifest",
    "CometKiwiModelStatus",
    "OptionalEstimatorDependencyMissing",
)


class OptionalEstimatorDependencyMissing(RuntimeError):
    """Raised when the estimator is asked for but cannot run on this machine."""


class CometKiwiLicenseNotAccepted(OptionalEstimatorDependencyMissing):
    """Raised when an install is attempted before the licence was accepted."""


@dataclass(frozen=True, slots=True)
class CometKiwiModelStatus:
    """What is installed, how big it is, and why it cannot be used if it cannot."""

    state: Literal["missing", "installing", "ready", "invalid"]
    model: str
    installed_size_bytes: int | None = None
    license_name: str = ""
    reason: str = ""

    def __post_init__(self) -> None:
        if self.state not in {"missing", "installing", "ready", "invalid"}:
            raise ValueError("unsupported model state")
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("model must be a nonempty string")
        if self.installed_size_bytes is not None and self.installed_size_bytes < 0:
            raise ValueError("installed_size_bytes must be non-negative")


@dataclass(frozen=True, slots=True)
class CometKiwiModelManifest:
    """One model version, named by everything a user must know before installing."""

    model: str
    source_url: str
    size_bytes: int
    sha256_by_file: Mapping[str, str]
    license_name: str
    license_url: str
    minimum_ram_bytes: int

    def __post_init__(self) -> None:
        for field_name in (
            "model",
            "source_url",
            "license_name",
            "license_url",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a nonempty string")
        for field_name in ("size_bytes", "minimum_ram_bytes"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{field_name} must be a positive integer")
        if not isinstance(self.sha256_by_file, Mapping) or not self.sha256_by_file:
            raise ValueError("sha256_by_file must be a nonempty mapping")
        for name, digest in self.sha256_by_file.items():
            if not isinstance(name, str) or "/" in name or name in {".", ".."}:
                raise ValueError("model file name must be a plain file name")
            if not isinstance(digest, str) or len(digest) != 64:
                raise ValueError("sha256_by_file values must be sha256 digests")

    def bundle(self) -> ModelBundle:
        """The shared installer's view: one file list, each named by its digest."""
        files = tuple(
            ModelBundleFile(
                name=name,
                url=f"{self.source_url.rstrip('/')}/{name}",
                size_bytes=max(1, self.size_bytes // len(self.sha256_by_file)),
                sha256=digest,
            )
            for name, digest in sorted(self.sha256_by_file.items())
        )
        return ModelBundle(version=self.model, files=files)


class CometKiwiModelManager:
    """Own the COMETKiwi model directory, and refuse to fill it uninvited."""

    def __init__(
        self,
        root: Path | str,
        manifest: CometKiwiModelManifest,
        downloader=None,
    ) -> None:
        if not isinstance(manifest, CometKiwiModelManifest):
            raise TypeError("manifest must be a CometKiwiModelManifest")
        self.manifest = manifest
        self._bundle = ModelBundleManager(
            root,
            manifest.bundle(),
            downloader,
            error_type=OptionalEstimatorDependencyMissing,
            staging_prefix=".cometkiwi-",
        )

    @property
    def root(self) -> Path:
        return self._bundle.root

    @property
    def model_dir(self) -> Path:
        return self._bundle.model_dir

    def status(self) -> CometKiwiModelStatus:
        """Report what is on disk without reading a single weight byte."""
        return self._as_status(self._bundle.status())

    async def install(
        self,
        manifest: CometKiwiModelManifest | None = None,
        *,
        license_accepted: bool = False,
        progress=None,
        cancellation=None,
    ) -> CometKiwiModelStatus:
        """Download and verify the weights, once the licence has been accepted."""
        if manifest is not None and manifest != self.manifest:
            raise OptionalEstimatorDependencyMissing("manifest_mismatch")
        if not license_accepted:
            raise CometKiwiLicenseNotAccepted("license_not_accepted")
        return self._as_status(
            await self._bundle.install(progress=progress, cancellation=cancellation)
        )

    def uninstall(self) -> CometKiwiModelStatus:
        """Delete exactly this model directory and nothing around it."""
        return self._as_status(self._bundle.uninstall())

    def _as_status(self, status) -> CometKiwiModelStatus:
        return CometKiwiModelStatus(
            state=status.state,
            model=self.manifest.model,
            installed_size_bytes=status.installed_size_bytes,
            license_name=self.manifest.license_name,
            reason=status.reason,
        )


def describe_cometkiwi_setup(
    settings,
    status: CometKiwiModelStatus | None = None,
    last_duration_seconds: float | None = None,
) -> str:
    """One line saying what is set up, what is missing, and what it last cost.

    Ticking the checkbox is not the same as having the estimator: this text is
    what tells the user which of the three parts — runner, weights, licence —
    they still owe, before anything is downloaded or started.
    """
    if not getattr(settings.capabilities, "cometkiwi_enabled", False):
        return ""
    missing: list[str] = []
    if not str(getattr(settings, "cometkiwi_runner_path", "") or "").strip():
        missing.append("путь к runner")
    if not str(getattr(settings, "cometkiwi_model", "") or "").strip():
        missing.append("модель")
    if not getattr(settings, "cometkiwi_license_accepted", False):
        missing.append("принятая лицензия")
    if status is None or status.state != "ready":
        missing.append("установленные веса")
    if missing:
        return "COMETKiwi: требует настройки — не хватает: " + ", ".join(missing) + "."

    parts = [
        f"COMETKiwi: {status.model}",
        f"устройство {getattr(settings, 'cometkiwi_device', 'cpu')}",
        f"лицензия {status.license_name}" if status.license_name else "",
        _size_text(status.installed_size_bytes),
        "нагрузка: high (cpu, memory)",
    ]
    if last_duration_seconds is not None:
        parts.append(f"последний запуск {last_duration_seconds:.1f} c")
    return " · ".join(part for part in parts if part) + "."


def _size_text(size_bytes: int | None) -> str:
    if not size_bytes:
        return ""
    return f"{size_bytes / 1024 ** 3:.1f} ГБ на диске"
