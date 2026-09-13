import contextlib
import json

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


def _problem_terms(*item_ids):
    return json.dumps(
        [
            {"id": item_id, "term": "Термин", "internal_html_path": "Text/chapter.xhtml"}
            for item_id in item_ids
        ],
        ensure_ascii=False,
    )


def _without_zstd(monkeypatch):
    monkeypatch.setattr(project_manager, "_zstd", None)


@pytest.mark.parametrize(
    "filename, previous, save, prepare",
    [
        pytest.param(
            "term_frequency_cache.json",
            '{"terms": {}}',
            lambda manager: manager.save_term_frequency_cache({"terms": {"альфа": 3}}),
            None,
            id="term-frequency-cache",
        ),
        pytest.param(
            "chapter_analysis_cache.json",
            '{"chapters": {}}',
            lambda manager: manager.save_chapter_analysis_cache(
                {"chapters": {"Text/chapter.xhtml": {}}}
            ),
            None,
            id="chapter-analysis-cache",
        ),
        pytest.param(
            "chapter_size_cache.json",
            "{}",
            lambda manager: manager.save_size_cache({"Text/chapter.xhtml": 1200}),
            None,
            id="chapter-size-cache",
        ),
        pytest.param(
            "glossary_generation_map.json",
            '["Text/old.xhtml"]',
            lambda manager: manager.save_glossary_generation_map({"Text/new.xhtml"}),
            None,
            id="glossary-generation-map",
        ),
        pytest.param(
            "user_problem_terms.json",
            _problem_terms("old"),
            lambda manager: manager.upsert_user_problem_terms([{"id": "new", "term": "Новый"}]),
            None,
            id="problem-terms-upsert",
        ),
        pytest.param(
            "user_problem_terms.json",
            _problem_terms("old", "stale"),
            lambda manager: manager.remove_user_problem_terms(["stale"]),
            None,
            id="problem-terms-remove",
        ),
        pytest.param(
            "validation_analysis_cache.json",
            '{"Text/old.xhtml": {}}',
            lambda manager: manager.save_validation_cache({"Text/new.xhtml": {}}),
            _without_zstd,
            id="validation-cache-without-zstd",
        ),
    ],
)
def test_failed_save_keeps_previous_project_file(
    tmp_path, monkeypatch, filename, previous, save, prepare
):
    target = tmp_path / filename
    target.write_text(previous, encoding="utf-8")
    manager = TranslationProjectManager(tmp_path)
    if prepare is not None:
        prepare(monkeypatch)
    monkeypatch.setattr(project_manager.os, "replace", _fail_replace)

    with contextlib.suppress(OSError):
        save(manager)

    assert target.read_text(encoding="utf-8") == previous
    assert list(tmp_path.glob("*.tmp")) == []


@pytest.mark.parametrize(
    "save, load, expected",
    [
        pytest.param(
            lambda manager: manager.save_term_frequency_cache({"terms": {"альфа": 3}}),
            lambda manager: manager.load_term_frequency_cache(),
            {"terms": {"альфа": 3}},
            id="term-frequency-cache",
        ),
        pytest.param(
            lambda manager: manager.save_chapter_analysis_cache(
                {"chapters": {"Text/chapter.xhtml": {"words": 10}}}
            ),
            lambda manager: manager.load_chapter_analysis_cache(),
            {"chapters": {"Text/chapter.xhtml": {"words": 10}}},
            id="chapter-analysis-cache",
        ),
        pytest.param(
            lambda manager: manager.save_size_cache({"Text/chapter.xhtml": 1200}),
            lambda manager: manager.load_size_cache(),
            {"Text/chapter.xhtml": 1200},
            id="chapter-size-cache",
        ),
        pytest.param(
            lambda manager: manager.save_glossary_generation_map(
                {"Text/b.xhtml", "Text/a.xhtml"}
            ),
            lambda manager: manager.load_glossary_generation_map(),
            {"Text/a.xhtml", "Text/b.xhtml"},
            id="glossary-generation-map",
        ),
        pytest.param(
            lambda manager: manager.upsert_user_problem_terms([{"id": "new", "term": "Новый"}]),
            lambda manager: manager.load_user_problem_terms(),
            [{"id": "new", "term": "Новый"}],
            id="problem-terms",
        ),
    ],
)
def test_saved_project_file_reads_back(tmp_path, save, load, expected):
    save(TranslationProjectManager(tmp_path))

    assert load(TranslationProjectManager(tmp_path)) == expected
