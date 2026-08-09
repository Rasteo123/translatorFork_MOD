import os
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from gemini_translator.ui.dialogs.epub import EpubHtmlSelectorDialog


def _qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_chapter_selection_tooltip_uses_h1_title(tmp_path):
    app = _qapp()
    epub_path = tmp_path / "book.epub"
    chapter_path = "OEBPS/chapter001.xhtml"
    chapter_title = "Chapter 1: Start"

    with zipfile.ZipFile(epub_path, "w") as archive:
        archive.writestr(
            chapter_path,
            f"<html><body><h1>{chapter_title}</h1><p>Body text.</p></body></html>",
        )

    dialog = EpubHtmlSelectorDialog(str(epub_path))
    try:
        dialog.virtual_epub_path = str(epub_path)
        dialog.all_chapters = [chapter_path]
        dialog.list_widget = QtWidgets.QListWidget()
        dialog._load_chapter_title_cache()
        # Кэш заголовков теперь заполняется лениво батчами — прогоняем скан
        # синхронно до конца (по батчу за вызов), как это сделала бы очередь событий.
        while dialog._title_scan_index is not None:
            dialog._scan_chapter_titles_batch()

        dialog._populate_list_widget_preview()
        preview_tooltip = dialog.list_widget.item(0).toolTip()
        assert f"H1: {chapter_title}" in preview_tooltip
        assert chapter_path in preview_tooltip

        dialog._size_cache = {chapter_path: 123}
        dialog._populate_list_widget(dialog.all_chapters)
        final_tooltip = dialog.list_widget.item(0).toolTip()
        assert f"H1: {chapter_title}" in final_tooltip
        assert chapter_path in final_tooltip
    finally:
        dialog.virtual_epub_path = None
        dialog.close()
