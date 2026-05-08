import os
import zipfile

from gemini_translator.cli import (
    _choose_translation_rel_path,
    build_task_plan,
    select_chapters,
)
from gemini_translator.utils.project_manager import TranslationProjectManager


def _build_epub(path):
    with zipfile.ZipFile(path, "w") as epub:
        epub.writestr("mimetype", "application/epub+zip")
        epub.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
""",
        )
        epub.writestr(
            "OEBPS/content.opf",
            """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <manifest>
    <item id="ch1" href="ch1.xhtml" media-type="application/xhtml+xml"/>
    <item id="ch2" href="ch2.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="ch1"/>
    <itemref idref="ch2"/>
  </spine>
</package>
""",
        )
        epub.writestr("OEBPS/ch1.xhtml", "<html><body><p>One</p></body></html>")
        epub.writestr("OEBPS/ch2.xhtml", "<html><body><p>Two</p></body></html>")


def test_select_chapters_pending_skips_project_map_entries(tmp_path):
    epub_path = tmp_path / "book.epub"
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    _build_epub(epub_path)

    translated = project_dir / "OEBPS" / "ch1_translated.html"
    translated.parent.mkdir()
    translated.write_text("<html><body><p>One translated</p></body></html>", encoding="utf-8")

    manager = TranslationProjectManager(str(project_dir))
    manager.register_translation(
        "OEBPS/ch1.xhtml",
        "_translated.html",
        os.path.relpath(translated, project_dir).replace("\\", "/"),
    )

    assert select_chapters(str(epub_path), manager, mode="pending") == ["OEBPS/ch2.xhtml"]
    assert select_chapters(str(epub_path), manager, mode="translated") == ["OEBPS/ch1.xhtml"]


def test_build_task_plan_uses_batch_mode(tmp_path):
    epub_path = tmp_path / "book.epub"
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    _build_epub(epub_path)

    settings = {
        "file_path": str(epub_path),
        "output_folder": str(project_dir),
        "use_batching": True,
        "chunking": False,
        "task_size_limit": 10000,
    }
    chapters = ["OEBPS/ch1.xhtml", "OEBPS/ch2.xhtml"]

    plan = build_task_plan(str(epub_path), chapters, settings, TranslationProjectManager(str(project_dir)))

    assert plan.summary["task_count"] == 1
    assert plan.summary["task_types"] == {"epub_batch": 1}
    assert plan.payloads[0][2] == tuple(chapters)


def test_choose_translation_rel_path_prefers_explicit_suffix():
    versions = {
        "_translated.html": "a.html",
        "_validated.html": "b.html",
    }

    assert _choose_translation_rel_path(versions, "_translated.html") == "a.html"
    assert _choose_translation_rel_path(versions) == "b.html"
