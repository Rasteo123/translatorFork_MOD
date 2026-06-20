import json

from gemini_translator.utils.project_manager import TranslationProjectManager


def _write_text(path, text="data"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_reused_project_cleanup_removes_old_chapters_outside_oebps_text(tmp_path):
    translation_map = {
        "OEBPS/Text/ch1.xhtml": {
            "_translated.html": "OEBPS/Text/ch1_translated.html",
        },
        "OPS/Text/ch2.xhtml": {
            "_translated.html": "OPS/Text/ch2_translated.html",
        },
    }
    (tmp_path / "translation_map.json").write_text(
        json.dumps(translation_map),
        encoding="utf-8",
    )

    _write_text(tmp_path / "OEBPS" / "Text" / "ch1.xhtml")
    _write_text(tmp_path / "OEBPS" / "Text" / "ch1_translated.html")
    _write_text(tmp_path / "OPS" / "Text" / "ch2.xhtml")
    _write_text(tmp_path / "OPS" / "Text" / "ch2_translated.html")
    _write_text(tmp_path / "Text" / "orphan_translated.html")
    _write_text(tmp_path / "OPS" / "Text" / "cover.jpg", "image")
    _write_text(tmp_path / "notes.html", "not a chapter")

    manager = TranslationProjectManager(str(tmp_path))
    targets = manager.find_reused_project_cleanup_targets()

    assert str(tmp_path / "OPS" / "Text" / "ch2_translated.html") in targets["files"]

    result = manager.cleanup_reused_project_chapter_outputs()

    assert result["failed"] == []
    assert result["removed_entries"] == 2
    assert not (tmp_path / "OEBPS" / "Text" / "ch1.xhtml").exists()
    assert not (tmp_path / "OEBPS" / "Text" / "ch1_translated.html").exists()
    assert not (tmp_path / "OPS" / "Text" / "ch2.xhtml").exists()
    assert not (tmp_path / "OPS" / "Text" / "ch2_translated.html").exists()
    assert not (tmp_path / "Text" / "orphan_translated.html").exists()
    assert (tmp_path / "OPS" / "Text" / "cover.jpg").exists()
    assert (tmp_path / "notes.html").exists()
    assert json.loads((tmp_path / "translation_map.json").read_text(encoding="utf-8")) == {}
