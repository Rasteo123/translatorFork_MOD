import pytest

from gemini_translator.utils import project_manager
from gemini_translator.utils.project_manager import TranslationProjectManager


def _fail_replace(*_args):
    raise OSError("disk full")


def test_translation_map_write_cleans_temporary_file_when_replace_fails(
    tmp_path, monkeypatch
):
    manager = TranslationProjectManager(tmp_path)
    manager.data = {"Text/chapter.xhtml": {"_translated.html": "out/chapter.html"}}
    monkeypatch.setattr(project_manager.os, "replace", _fail_replace)

    with pytest.raises(OSError, match="disk full"):
        manager.save()

    assert list(tmp_path.glob("translation_map.json*.tmp")) == []


def test_version_map_write_cleans_temporary_file_when_replace_fails(
    tmp_path, monkeypatch
):
    manager = TranslationProjectManager(tmp_path)
    monkeypatch.setattr(project_manager.os, "replace", _fail_replace)

    with pytest.raises(OSError, match="disk full"):
        manager.save_version_map({"Term": []})

    assert list(tmp_path.glob("glossary_versions.json*.tmp")) == []


@pytest.mark.skipif(
    project_manager._zstd is None,
    reason="zstandard is required for the compressed-cache write path",
)
def test_validation_cache_write_cleans_temporary_file_when_replace_fails(
    tmp_path, monkeypatch
):
    manager = TranslationProjectManager(tmp_path)
    monkeypatch.setattr(project_manager.os, "replace", _fail_replace)

    with pytest.raises(OSError, match="disk full"):
        manager.save_validation_cache({"chapter": {"ratio": 1.0}})

    assert list(tmp_path.glob("validation_analysis_cache.json.zst*.tmp")) == []
