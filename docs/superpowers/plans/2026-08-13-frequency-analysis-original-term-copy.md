# Frequency Analysis Original-Term Copy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the AI-correction-style “Копировать текст” context menu to original-term cells in both frequency-analysis tables.

**Architecture:** `TermFrequencyAnalyzerPage` will configure every table it creates with Qt's custom context-menu policy and route both signals to one page-level handler. The handler will accept the source table and click position, reject empty/non-original cells, and reuse the existing AI-correction pattern of `QMenu.exec()` followed by `QApplication.clipboard().setText()`.

**Tech Stack:** Python 3.10+, PyQt6, pytest, unittest.mock

## Global Constraints

- Apply the behavior to both the “Кандидаты” and “Частые” tables.
- Use the menu action label “Копировать текст”, matching the AI-correction glossary results window.
- Show the menu only for an existing cell in the “Оригинал” column.
- Copy the exact original-term text into the system clipboard.
- Do not change deletion checkboxes, translation/note editing, sorting, or pagination.

---

### Task 1: Original-Term Context Menu for Both Tables

**Files:**
- Create: `tests/test_term_frequency_analyzer_context_menu.py`
- Modify: `gemini_translator/ui/dialogs/glossary_dialogs/term_frequency_analyzer.py:4-15,157-183`

**Interfaces:**
- Consumes: `QTableWidget.customContextMenuRequested`, `QTableWidget.itemAt(pos)`, `QApplication.clipboard()`.
- Produces: `TermFrequencyAnalyzerPage._show_original_term_context_menu(table: QTableWidget, pos: QPoint) -> None`.

- [x] **Step 1: Write failing tests for table wiring and clipboard behavior**

Create `tests/test_term_frequency_analyzer_context_menu.py` with a headless `QApplication`, a lightweight fake menu whose `exec()` chooses its only action, and these tests:

```python
from types import SimpleNamespace
from unittest.mock import patch

from PyQt6 import QtCore, QtWidgets

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
```

- [x] **Step 2: Run the focused test and verify RED**

Run:

```bash
QT_QPA_PLATFORM=offscreen pytest -q tests/test_term_frequency_analyzer_context_menu.py
```

Expected: FAIL because `QMenu` is not exported by the target module, both tables still use the default context-menu policy, and `_show_original_term_context_menu` does not exist.

- [x] **Step 3: Implement the minimal shared context-menu handler**

In `term_frequency_analyzer.py`, import `QApplication` and `QMenu` from `PyQt6.QtWidgets`. In `_create_table`, configure and connect every created table:

```python
table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
table.customContextMenuRequested.connect(
    lambda pos, source_table=table: self._show_original_term_context_menu(source_table, pos)
)
```

Add the shared handler next to `_create_table`:

```python
def _show_original_term_context_menu(self, table, pos):
    item = table.itemAt(pos)
    if item is None or item.column() != 0:
        return

    menu = QMenu(self)
    copy_action = menu.addAction("Копировать текст")
    action = menu.exec(table.viewport().mapToGlobal(pos))
    if action == copy_action:
        QApplication.clipboard().setText(item.text())
```

- [x] **Step 4: Run the focused test and verify GREEN**

Run:

```bash
QT_QPA_PLATFORM=offscreen pytest -q tests/test_term_frequency_analyzer_context_menu.py
```

Expected: `3 passed` with exit code 0.

- [x] **Step 5: Run lint and the complete test suite**

Run:

```bash
ruff check gemini_translator/ui/dialogs/glossary_dialogs/term_frequency_analyzer.py tests/test_term_frequency_analyzer_context_menu.py
pytest -q
```

Expected: both commands exit 0 with no lint errors or failed tests.

Execution note: the complete suite passed. Full-file Ruff checking reports the same 16 pre-existing `E701`, `E702`, and `F841` violations on `main`; targeted checking of the new test and checking the modified module with those baseline codes excluded both pass.

- [x] **Step 6: Review the final diff and commit the implementation**

Run:

```bash
git diff --check
git diff -- gemini_translator/ui/dialogs/glossary_dialogs/term_frequency_analyzer.py tests/test_term_frequency_analyzer_context_menu.py
git add gemini_translator/ui/dialogs/glossary_dialogs/term_frequency_analyzer.py tests/test_term_frequency_analyzer_context_menu.py docs/superpowers/plans/2026-08-13-frequency-analysis-original-term-copy.md
git commit -m "feat(glossary): copy frequency-analysis terms"
```

Expected: the diff contains only the shared context-menu implementation, its regression tests, and this plan; the commit succeeds.
