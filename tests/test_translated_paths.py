import os

from gemini_translator.utils.translated_paths import (
    SAFE_FILENAME_MAX,
    build_translated_output_path,
    build_translated_relative_path,
)


def test_build_translated_output_path_keeps_short_name(tmp_path):
    path = build_translated_output_path(
        str(tmp_path),
        "OEBPS/Text/chapter_01.xhtml",
        "_translated_gemini.html",
    )

    assert path == os.path.join(
        str(tmp_path),
        "OEBPS",
        "Text",
        "chapter_01_translated_gemini.html",
    )


def test_build_translated_output_path_shortens_overlong_filename(tmp_path):
    long_stem = "0000_" + ("very_long_chapter_title_" * 20)
    path = build_translated_output_path(
        str(tmp_path),
        f"OEBPS/Text/{long_stem}.xhtml",
        "_translated_gemini.html",
    )

    filename = os.path.basename(path)
    assert filename.endswith("_translated_gemini.html")
    assert "__" in filename
    assert len(filename) <= SAFE_FILENAME_MAX


def test_build_translated_relative_path_matches_shortened_output(tmp_path):
    long_stem = "0000_" + ("chapter_" * 40)
    internal_path = f"OEBPS/Text/{long_stem}.xhtml"
    suffix = "_translated_gemini.html"

    full_path = build_translated_output_path(str(tmp_path), internal_path, suffix)
    rel_path = build_translated_relative_path(str(tmp_path), internal_path, suffix)

    assert rel_path == os.path.relpath(full_path, tmp_path).replace("\\", "/")
