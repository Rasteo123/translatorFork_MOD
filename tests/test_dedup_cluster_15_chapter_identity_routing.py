"""
Маршрутизационные тесты для cluster-15: chapter_identity.

Каноническая реализация: gemini_translator.utils.chapter_identity
.chapter_identity. ChapterSelectionDialog и ConsistencyValidatorPage
обращаются к ней через модуль (``chapter_identity_utils.chapter_identity``),
а не через ребинд локального имени — поэтому патч канонической функции
реально перехватывает вызовы из обоих мест.

До рефакторинга оба места определяли собственную копию
(``_chapter_identity`` / ``_chapter_id``) и не видели патч канонической
функции — тест падает (RED). После рефакторинга оба вызывают канонический
хелпер и патч перехватывает вызов (GREEN).
"""
import os
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets  # noqa: E402

from gemini_translator.ui.dialogs.chapter_selection_dialog import (  # noqa: E402
    ChapterSelectionDialog,
)
from gemini_translator.ui.dialogs.consistency_checker import (  # noqa: E402
    ConsistencyValidatorPage,
)

_APP_REF = None


def _ensure_qapp():
    global _APP_REF
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    _APP_REF = app  # держим ссылку — иначе PyQt6 может собрать QApplication
    return app


CANONICAL_PATH = "gemini_translator.utils.chapter_identity.chapter_identity"


# ─── ChapterSelectionDialog ───────────────────────────────────────────────

def test_chapter_selection_dialog_restore_selection_routes_through_canonical():
    _ensure_qapp()
    chapters = [{"name": "Глава 1", "path": "Text/ch1.xhtml"}]

    with patch(CANONICAL_PATH, return_value="Text/ch1.xhtml") as mocked:
        dialog = ChapterSelectionDialog(
            chapters, previous_selection=["Text/ch1.xhtml"]
        )
        try:
            mocked.assert_any_call(chapters[0])
            item = dialog.chapter_list.item(0)
            assert item.checkState() == __import__(
                "PyQt6.QtCore", fromlist=["Qt"]
            ).Qt.CheckState.Checked
        finally:
            dialog.deleteLater()


# ─── ConsistencyValidatorPage ─────────────────────────────────────────────

class _PageStub:
    """Минимальная заглушка без тяжёлого __init__ ConsistencyValidatorPage."""

    _all_chapter_ids = ConsistencyValidatorPage._all_chapter_ids
    _get_selected_chapters = ConsistencyValidatorPage._get_selected_chapters

    def __init__(self, chapters, selected_chapter_ids=None):
        self.chapters = chapters
        self.selected_chapter_ids = set(selected_chapter_ids or ())


def test_all_chapter_ids_routes_through_canonical():
    chapters = [
        {"name": "Глава 1", "path": "Text/ch1.xhtml"},
        {"name": "Глава 2", "path": "Text/ch2.xhtml"},
    ]
    page = _PageStub(chapters)

    with patch(CANONICAL_PATH, side_effect=["id-1", "id-2"]) as mocked:
        result = page._all_chapter_ids()

    assert mocked.call_count == 2
    assert result == ["id-1", "id-2"]


def test_get_selected_chapters_routes_through_canonical():
    chapters = [
        {"name": "Глава 1", "path": "Text/ch1.xhtml"},
        {"name": "Глава 2", "path": "Text/ch2.xhtml"},
    ]
    page = _PageStub(chapters, selected_chapter_ids={"Text/ch2.xhtml"})

    with patch(
        CANONICAL_PATH,
        side_effect=lambda ch: ch["path"],
    ) as mocked:
        result = page._get_selected_chapters()

    assert mocked.call_count == 2
    assert result == [chapters[1]]
