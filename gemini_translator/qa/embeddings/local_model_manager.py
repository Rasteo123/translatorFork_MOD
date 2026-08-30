"""The optional local embedding model as one explicitly installed bundle."""

from __future__ import annotations

from pathlib import Path

from ..model_bundle import (
    MANIFEST_NAME,
    ModelBundle,
    ModelBundleFile,
    ModelBundleManager,
    ModelBundleStatus,
)
from .base import EmbeddingContractError


class OptionalEmbeddingDependencyMissing(EmbeddingContractError):
    """Raised when a local embedding path is chosen but cannot run here.

    The message names what is missing — the packages or the weights — so the
    user can fix it, and the online providers stay untouched either way.
    """


LocalEmbeddingModelFile = ModelBundleFile
LocalEmbeddingManifest = ModelBundle
LocalEmbeddingModelStatus = ModelBundleStatus

__all__ = (
    "MANIFEST_NAME",
    "LocalEmbeddingManifest",
    "LocalEmbeddingModelFile",
    "LocalEmbeddingModelManager",
    "LocalEmbeddingModelStatus",
    "OptionalEmbeddingDependencyMissing",
)


class LocalEmbeddingModelManager(ModelBundleManager):
    """Install the local embedding weights, and nothing else, when asked."""

    def __init__(self, root: Path | str, manifest: ModelBundle, downloader=None) -> None:
        super().__init__(
            root,
            manifest,
            downloader,
            error_type=OptionalEmbeddingDependencyMissing,
            staging_prefix=".embedding-",
        )
