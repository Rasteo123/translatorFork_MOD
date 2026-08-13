from types import SimpleNamespace
from unittest.mock import patch

from PyQt6 import QtCore, QtWidgets

import gemini_translator.ui.dialogs.validation  # noqa: F401 — initializes UI imports
from gemini_translator.ui.dialogs.glossary_dialogs import term_frequency_analyzer as module


class _CopyMenu:
    def __init__(self, parent):
        self.action = None

    def addAction(self, text):
        assert text == "Копировать текст"
        self.action = object()
        return self.action

    def exec(self, global_pos):
        return self.action


class _Viewport:
    def mapToGlobal(self, pos):
        return pos


class _Table:
    def __init__(self, item):
        self._item = item
        self._viewport = _Viewport()

    def itemAt(self, pos):
        return self._item

    def viewport(self):
        return self._viewport


def _item(column, text):
    return SimpleNamespace(column=lambda: column, text=lambda: text)


def test_both_frequency_tables_enable_custom_context_menus(monkeypatch):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    monkeypatch.setattr(module.QtCore.QTimer, "singleShot", lambda *args: None)
    page = module.TermFrequencyAnalyzerPage(
        [{"original": "魂聖", "rus": "духовный святой", "note": ""}],
        epub_path="unused.epub",
    )
    try:
        for table in (page.rare_table, page.freq_table):
            assert table.contextMenuPolicy() == QtCore.Qt.ContextMenuPolicy.CustomContextMenu
            assert table.receivers(table.customContextMenuRequested) == 1
    finally:
        page.deleteLater()
        app.processEvents()


def test_original_term_action_copies_exact_text():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.clipboard().clear()
    table = _Table(_item(0, "魂聖"))

    with patch.object(module, "QMenu", _CopyMenu):
        module.TermFrequencyAnalyzerPage._show_original_term_context_menu(
            SimpleNamespace(), table, QtCore.QPoint(4, 7)
        )

    assert app.clipboard().text() == "魂聖"


def test_context_menu_ignores_non_original_and_empty_cells():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.clipboard().setText("unchanged")

    with patch.object(module, "QMenu", _CopyMenu):
        for item in (_item(1, "перевод"), None):
            module.TermFrequencyAnalyzerPage._show_original_term_context_menu(
                SimpleNamespace(), _Table(item), QtCore.QPoint()
            )

    assert app.clipboard().text() == "unchanged"
