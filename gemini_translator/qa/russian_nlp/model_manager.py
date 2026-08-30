"""The Slovnet weights as one explicitly installed, verifiable model bundle."""

from __future__ import annotations

from pathlib import Path

from ..model_bundle import (
    MANIFEST_NAME,
    MODEL_STATES,
    ModelBundle,
    ModelBundleFile,
    ModelBundleManager,
    ModelBundleStatus,
)
from .base import RussianNlpUnavailable


# The plan names these after the model they carry; they are the generic bundle
# types with this analyzer's own failure type attached.
SlovnetManifest = ModelBundle
SlovnetModelFile = ModelBundleFile
SlovnetModelStatus = ModelBundleStatus

__all__ = (
    "MANIFEST_NAME",
    "MODEL_STATES",
    "SlovnetManifest",
    "SlovnetModelFile",
    "SlovnetModelManager",
    "SlovnetModelStatus",
)


class SlovnetModelManager(ModelBundleManager):
    """Install the Russian NLP weights, reporting failures as an NLP outage."""

    def __init__(self, root: Path | str, manifest: ModelBundle, downloader=None) -> None:
        super().__init__(
            root,
            manifest,
            downloader,
            error_type=RussianNlpUnavailable,
            staging_prefix=".slovnet-",
        )
