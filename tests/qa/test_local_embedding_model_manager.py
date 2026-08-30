"""The local embedding weights install explicitly, verified and reversible."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import pytest

from gemini_translator.qa.embeddings.local_model_manager import (
    LocalEmbeddingManifest,
    LocalEmbeddingModelFile,
    LocalEmbeddingModelManager,
    OptionalEmbeddingDependencyMissing,
)


_MODEL = b"onnx model bytes"
_TOKENIZER = b"tokenizer json bytes"


def _manifest(version: str = "e5-small-v1") -> LocalEmbeddingManifest:
    return LocalEmbeddingManifest(
        version=version,
        files=(
            LocalEmbeddingModelFile(
                name="model.onnx",
                url="https://example.test/model.onnx",
                size_bytes=len(_MODEL),
                sha256=hashlib.sha256(_MODEL).hexdigest(),
            ),
            LocalEmbeddingModelFile(
                name="tokenizer.json",
                url="https://example.test/tokenizer.json",
                size_bytes=len(_TOKENIZER),
                sha256=hashlib.sha256(_TOKENIZER).hexdigest(),
            ),
        ),
    )


def _downloader(overrides=None):
    payloads = {
        "https://example.test/model.onnx": _MODEL,
        "https://example.test/tokenizer.json": _TOKENIZER,
    }
    payloads.update(overrides or {})
    return lambda url: payloads[url]


def test_nothing_is_downloaded_until_install_is_called(tmp_path: Path):
    """Choosing the local provider must not start a download by itself."""
    calls = []
    manager = LocalEmbeddingModelManager(
        tmp_path, _manifest(), downloader=lambda url: calls.append(url)
    )

    assert manager.status().state == "missing"
    assert calls == []


def test_a_verified_install_is_ready_and_atomic(tmp_path: Path):
    """A partially downloaded model must never look installed."""
    manager = LocalEmbeddingModelManager(tmp_path, _manifest(), downloader=_downloader())

    status = asyncio.run(manager.install())

    assert status.state == "ready"
    assert (manager.model_dir / "model.onnx").read_bytes() == _MODEL
    assert not list(tmp_path.glob(".embedding-*"))


def test_a_wrong_hash_removes_only_the_staging_directory(tmp_path: Path):
    """A corrupted download must leave the cache and the journal alone."""
    keep = tmp_path / "translation_qa_embedding_cache"
    keep.mkdir()
    (keep / "vector.json").write_text("cached", encoding="utf-8")
    manager = LocalEmbeddingModelManager(
        tmp_path,
        _manifest(),
        downloader=_downloader({"https://example.test/model.onnx": b"corrupted"}),
    )

    with pytest.raises(OptionalEmbeddingDependencyMissing):
        asyncio.run(manager.install())

    assert manager.status().state == "missing"
    assert (keep / "vector.json").read_text(encoding="utf-8") == "cached"
    assert not list(tmp_path.glob(".embedding-*"))


def test_cancellation_leaves_no_partial_model(tmp_path: Path):
    """A cancelled download must not leave something that loads and fails."""

    class _Token:
        def __init__(self) -> None:
            self.calls = 0

        def raise_if_cancelled(self):
            self.calls += 1
            if self.calls > 1:
                raise asyncio.CancelledError

    manager = LocalEmbeddingModelManager(tmp_path, _manifest(), downloader=_downloader())

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(manager.install(cancellation=_Token()))

    assert manager.status().state == "missing"


def test_uninstall_removes_the_model_and_nothing_else(tmp_path: Path):
    """Removing a model must never take the embedding cache with it."""
    cache = tmp_path / "translation_qa_embedding_cache"
    cache.mkdir()
    (cache / "vector.json").write_text("cached", encoding="utf-8")
    manager = LocalEmbeddingModelManager(tmp_path, _manifest(), downloader=_downloader())
    asyncio.run(manager.install())

    status = manager.uninstall()

    assert status.state == "missing"
    assert not manager.model_dir.exists()
    assert (cache / "vector.json").read_text(encoding="utf-8") == "cached"


def test_a_missing_file_is_invalid_not_ready(tmp_path: Path):
    """An incomplete model directory must be reported, never loaded."""
    manager = LocalEmbeddingModelManager(tmp_path, _manifest(), downloader=_downloader())
    asyncio.run(manager.install())
    (manager.model_dir / "tokenizer.json").unlink()

    status = manager.status()

    assert status.state == "invalid"
    assert status.reason == "missing:tokenizer.json"


def test_installing_without_a_downloader_is_refused(tmp_path: Path):
    """The application must never invent its own way to fetch a model."""
    manager = LocalEmbeddingModelManager(tmp_path, _manifest())

    with pytest.raises(OptionalEmbeddingDependencyMissing):
        asyncio.run(manager.install())
