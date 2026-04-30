import os
import shutil
from pathlib import Path

from gemini_translator.utils.batch_markers import find_boundary_markers
from gemini_translator.utils.translation_versions import select_target_translation_version


def _fresh_tmp_dir(name):
    path = Path("tests") / ".tmp_validation_version" / name
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


def test_validation_selects_newest_nonvalidated_translation():
    tmp_path = _fresh_tmp_dir("newest")
    gemini_path = tmp_path / "Text" / "ch1_translated_gemini.html"
    deepseek_path = tmp_path / "Text" / "ch1_translated_dp.html"
    gemini_path.parent.mkdir()
    try:
        gemini_path.write_text("<html><body><p>old</p></body></html>", encoding="utf-8")
        deepseek_path.write_text("<html><body><p>new</p></body></html>", encoding="utf-8")

        os.utime(gemini_path, (1000, 1000))
        os.utime(deepseek_path, (2000, 2000))

        rel_path, is_validated = select_target_translation_version(
            {
                "_translated_gemini.html": "Text/ch1_translated_gemini.html",
                "_translated_dp.html": "Text/ch1_translated_dp.html",
            },
            str(tmp_path),
        )

        assert rel_path == "Text/ch1_translated_dp.html"
        assert is_validated is False
    finally:
        shutil.rmtree(tmp_path.parent, ignore_errors=True)


def test_validation_keeps_validated_translation_preferred():
    tmp_path = _fresh_tmp_dir("validated")
    translated_path = tmp_path / "Text" / "ch1_translated_dp.html"
    validated_path = tmp_path / "Text" / "ch1_validated.html"
    translated_path.parent.mkdir()
    try:
        translated_path.write_text("<html><body><p>new</p></body></html>", encoding="utf-8")
        validated_path.write_text("<html><body><p>accepted</p></body></html>", encoding="utf-8")

        os.utime(validated_path, (1000, 1000))
        os.utime(translated_path, (2000, 2000))

        rel_path, is_validated = select_target_translation_version(
            {
                "_validated.html": "Text/ch1_validated.html",
                "_translated_dp.html": "Text/ch1_translated_dp.html",
            },
            str(tmp_path),
        )

        assert rel_path == "Text/ch1_validated.html"
        assert is_validated is True
    finally:
        shutil.rmtree(tmp_path.parent, ignore_errors=True)


def test_boundary_marker_selection_keeps_text_before_repeated_start_marker():
    response = (
        "<!-- 0 -->\n"
        "<p>Начало главы сохранено.</p>\n"
        "<!-- 0 -->\n"
        "<p>Остальная часть.</p>\n"
        "<!-- 1 -->"
    )

    markers = find_boundary_markers(response, chapter_count=1)
    extracted = response[markers[0][1]:markers[1][0]]

    assert "Начало главы сохранено" in extracted
    assert "Остальная часть" in extracted
