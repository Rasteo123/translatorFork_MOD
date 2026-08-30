"""Install an optional model bundle explicitly, verifiably, and reversibly."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Literal


MODEL_STATES = frozenset({"missing", "installing", "ready", "invalid"})
MANIFEST_NAME = "manifest.json"


@dataclass(frozen=True, slots=True)
class ModelBundleFile:
    """One weight file, named by what it must hash to."""

    name: str
    url: str
    size_bytes: int
    sha256: str

    def __post_init__(self) -> None:
        for field_name in ("name", "url", "sha256"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a nonempty string")
        if "/" in self.name or self.name in {".", ".."}:
            raise ValueError("model file name must be a plain file name")
        if isinstance(self.size_bytes, bool) or not isinstance(self.size_bytes, int):
            raise ValueError("size_bytes must be an integer")
        if self.size_bytes <= 0:
            raise ValueError("size_bytes must be positive")


@dataclass(frozen=True, slots=True)
class ModelBundle:
    """The exact set of files one model version consists of."""

    version: str
    files: tuple[ModelBundleFile, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.version, str) or not self.version.strip():
            raise ValueError("version must be a nonempty string")
        if not self.files:
            raise ValueError("a manifest needs at least one file")

    @property
    def total_bytes(self) -> int:
        return sum(item.size_bytes for item in self.files)


@dataclass(frozen=True, slots=True)
class ModelBundleStatus:
    """What is installed right now, and why it cannot be used if it cannot."""

    state: Literal["missing", "installing", "ready", "invalid"]
    version: str | None = None
    installed_size_bytes: int | None = None
    reason: str = ""

    def __post_init__(self) -> None:
        if self.state not in MODEL_STATES:
            raise ValueError("unsupported model state")


class ModelBundleManager:
    """Own one model directory: nothing is downloaded until asked, twice over.

    Enabling the capability never installs anything; only an explicit install
    does, into a temporary directory that is verified file by file and renamed
    into place at the end. A cancelled or corrupted install leaves whatever was
    already working untouched.
    """

    def __init__(
        self,
        root: Path | str,
        manifest: ModelBundle,
        downloader=None,
        *,
        error_type: type[Exception] = RuntimeError,
        staging_prefix: str = ".bundle-",
    ) -> None:
        if not isinstance(manifest, ModelBundle):
            raise TypeError("manifest must be a ModelBundle")
        self.root = Path(root)
        self.manifest = manifest
        self.error_type = error_type
        self._staging_prefix = staging_prefix
        self._downloader = downloader

    @property
    def model_dir(self) -> Path:
        return self.root / self.manifest.version

    def status(self) -> ModelBundleStatus:
        """Report the installed state without reading a single weight byte."""
        directory = self.model_dir
        if not directory.is_dir():
            return ModelBundleStatus("missing")
        installed = 0
        for item in self.manifest.files:
            path = directory / item.name
            if not path.is_file():
                return ModelBundleStatus(
                    "invalid", self.manifest.version, reason=f"missing:{item.name}"
                )
            installed += path.stat().st_size
        record = _read_manifest(directory)
        if record is not None and record.get("version") != self.manifest.version:
            return ModelBundleStatus(
                "invalid", self.manifest.version, installed, "version_mismatch"
            )
        return ModelBundleStatus("ready", self.manifest.version, installed)

    async def install(self, progress=None, cancellation=None) -> ModelBundleStatus:
        """Download and verify every file, then swap the directory in atomically."""
        if self._downloader is None:
            raise self.error_type("model_downloader_missing")
        if self.status().state == "ready":
            return self.status()
        self.root.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=self._staging_prefix, dir=str(self.root)))
        downloaded = 0
        try:
            for item in self.manifest.files:
                _raise_if_cancelled(cancellation)
                target = staging / item.name
                data = await _await_maybe(self._downloader(item.url))
                if not isinstance(data, (bytes, bytearray)):
                    raise self.error_type("model_download_invalid")
                digest = hashlib.sha256(data).hexdigest()
                if digest != item.sha256:
                    raise self.error_type(f"model_hash_mismatch:{item.name}")
                target.write_bytes(bytes(data))
                downloaded += len(data)
                _report(progress, item.name, downloaded, self.manifest.total_bytes)
            _raise_if_cancelled(cancellation)
            (staging / MANIFEST_NAME).write_text(
                json.dumps({"version": self.manifest.version}, ensure_ascii=False),
                encoding="utf-8",
            )
            destination = self.model_dir
            if destination.exists():
                shutil.rmtree(destination, ignore_errors=True)
            os.replace(staging, destination)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return self.status()

    def uninstall(self) -> ModelBundleStatus:
        """Delete exactly this model directory, and never anything above it."""
        directory = self.model_dir
        if directory.is_dir() and directory.parent == self.root:
            shutil.rmtree(directory, ignore_errors=True)
        return self.status()


def _read_manifest(directory: Path) -> Mapping[str, object] | None:
    try:
        payload = json.loads((directory / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _raise_if_cancelled(cancellation) -> None:
    if cancellation is None:
        return
    check = getattr(cancellation, "raise_if_cancelled", None)
    if callable(check):
        check()
        return
    if getattr(cancellation, "is_cancelled", False):
        raise asyncio.CancelledError


async def _await_maybe(value):
    if hasattr(value, "__await__"):
        return await value
    return value


def _report(progress, name: str, done: int, total: int) -> None:
    if not callable(progress):
        return
    try:
        progress(name, done, total)
    except Exception:  # noqa: BLE001 - progress reporting must never fail an install
        return
