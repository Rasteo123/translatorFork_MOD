"""Model weights are installed explicitly, verified by hash, and reversible."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from gemini_translator.qa.russian_nlp import (
    RussianNlpUnavailable,
    SlovnetManifest,
    SlovnetModelFile,
    SlovnetModelManager,
)


_NAVEC = b"navec weights"
_NER = b"ner weights"


def _manifest(version: str = "v1") -> SlovnetManifest:
    return SlovnetManifest(
        version=version,
        files=(
            SlovnetModelFile(
                name="navec.tar",
                url="https://example.test/navec.tar",
                size_bytes=len(_NAVEC),
                sha256=hashlib.sha256(_NAVEC).hexdigest(),
            ),
            SlovnetModelFile(
                name="ner.tar",
                url="https://example.test/ner.tar",
                size_bytes=len(_NER),
                sha256=hashlib.sha256(_NER).hexdigest(),
            ),
        ),
    )


def _downloader(payloads=None, error=None):
    data = payloads or {
        "https://example.test/navec.tar": _NAVEC,
        "https://example.test/ner.tar": _NER,
    }

    def download(url):
        if error is not None:
            raise error
        return data[url]

    return download


def test_nothing_is_downloaded_by_creating_a_manager(tmp_path: Path):
    """Enabling a capability must never start a download by itself."""
    calls = []

    manager = SlovnetModelManager(
        tmp_path, _manifest(), downloader=lambda url: calls.append(url)
    )

    assert manager.status().state == "missing"
    assert calls == []
    assert list(tmp_path.iterdir()) == []


def test_a_verified_install_becomes_ready_atomically(tmp_path: Path):
    """A half-written model directory must never be visible as installed."""
    manager = SlovnetModelManager(tmp_path, _manifest(), downloader=_downloader())
    seen = []

    status = asyncio.run(
        manager.install(progress=lambda name, done, total: seen.append((name, done, total)))
    )

    assert status.state == "ready"
    assert status.version == "v1"
    assert (manager.model_dir / "navec.tar").read_bytes() == _NAVEC
    assert [name for name, _done, _total in seen] == ["navec.tar", "ner.tar"]
    assert not list(tmp_path.glob(".slovnet-*"))


def test_a_wrong_hash_leaves_the_previous_version_untouched(tmp_path: Path):
    """A tampered or truncated download must never replace a working model."""
    manager = SlovnetModelManager(tmp_path, _manifest(), downloader=_downloader())
    asyncio.run(manager.install())
    good = (manager.model_dir / "ner.tar").read_bytes()

    broken = SlovnetModelManager(
        tmp_path,
        _manifest("v2"),
        downloader=_downloader(
            {
                "https://example.test/navec.tar": _NAVEC,
                "https://example.test/ner.tar": b"corrupted",
            }
        ),
    )

    with pytest.raises(RussianNlpUnavailable) as excinfo:
        asyncio.run(broken.install())

    assert "hash_mismatch" in excinfo.value.reason
    assert (manager.model_dir / "ner.tar").read_bytes() == good
    assert manager.status().state == "ready"
    assert not list(tmp_path.glob(".slovnet-*"))


def test_cancellation_leaves_nothing_behind(tmp_path: Path):
    """A cancelled install must not leave a partial directory to be loaded."""

    class _Token:
        def __init__(self) -> None:
            self.calls = 0

        def raise_if_cancelled(self):
            self.calls += 1
            if self.calls > 1:
                raise asyncio.CancelledError

    manager = SlovnetModelManager(tmp_path, _manifest(), downloader=_downloader())

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(manager.install(cancellation=_Token()))

    assert manager.status().state == "missing"
    assert not list(tmp_path.glob(".slovnet-*"))


def test_a_missing_file_makes_the_install_invalid_not_ready(tmp_path: Path):
    """A model directory missing a weight must be reported, not loaded."""
    manager = SlovnetModelManager(tmp_path, _manifest(), downloader=_downloader())
    asyncio.run(manager.install())
    (manager.model_dir / "ner.tar").unlink()

    status = manager.status()

    assert status.state == "invalid"
    assert status.reason == "missing:ner.tar"


def test_a_version_mismatch_is_invalid(tmp_path: Path):
    """A directory recorded under another version must not be trusted."""
    manager = SlovnetModelManager(tmp_path, _manifest(), downloader=_downloader())
    asyncio.run(manager.install())
    (manager.model_dir / "manifest.json").write_text(
        json.dumps({"version": "other"}), encoding="utf-8"
    )

    assert manager.status().state == "invalid"


def test_uninstall_only_touches_its_own_model_directory(tmp_path: Path):
    """Removing a model must never reach outside the directory it owns."""
    manager = SlovnetModelManager(tmp_path, _manifest(), downloader=_downloader())
    asyncio.run(manager.install())
    neighbour = tmp_path / "unrelated"
    neighbour.mkdir()
    (neighbour / "keep.txt").write_text("keep", encoding="utf-8")

    status = manager.uninstall()

    assert status.state == "missing"
    assert not manager.model_dir.exists()
    assert (neighbour / "keep.txt").read_text(encoding="utf-8") == "keep"


def test_installing_twice_is_a_no_op(tmp_path: Path):
    """A ready model must not be downloaded again on every launch."""
    calls = []

    def counting(url):
        calls.append(url)
        return {"https://example.test/navec.tar": _NAVEC, "https://example.test/ner.tar": _NER}[url]

    manager = SlovnetModelManager(tmp_path, _manifest(), downloader=counting)
    asyncio.run(manager.install())
    asyncio.run(manager.install())

    assert len(calls) == 2


def test_installing_without_a_downloader_is_refused(tmp_path: Path):
    """The application must never invent its own way to fetch weights."""
    manager = SlovnetModelManager(tmp_path, _manifest())

    with pytest.raises(RussianNlpUnavailable):
        asyncio.run(manager.install())


@pytest.mark.parametrize(
    "name",
    ["../escape.tar", "nested/file.tar", "", "   "],
)
def test_a_manifest_may_not_name_a_path(name):
    """A file name that walks the filesystem would install anywhere at all."""
    with pytest.raises(ValueError):
        SlovnetModelFile(name=name, url="https://x/y", size_bytes=1, sha256="abc")


def test_an_empty_manifest_is_refused():
    """A manifest with no files would report an empty directory as ready."""
    with pytest.raises(ValueError):
        SlovnetManifest(version="v1", files=())
