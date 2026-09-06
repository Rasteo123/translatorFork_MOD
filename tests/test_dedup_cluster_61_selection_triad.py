"""
Тесты для cluster-61: триада select_all/deselect_all/invert_selection была
продублирована в трёх местах (gemini_translator/ui/dialogs/
chapter_selection_dialog.py, gemini_translator/ui/dialogs/glossary_dialogs/
versioning.py, ranobelib/main_window.py) с почти идентичным алгоритмом
прохода по чекбоксам QListWidget.

Каноническая реализация (по ограничению scope этой волны новый модуль
selection_utils.py создать было нельзя — recommended_canonical не входил в
разрешённый список файлов): gemini_translator.ui.dialogs
.chapter_selection_dialog.set_checked_all / .invert_checked.

Поведение (behavior_choice):
- set_checked_all(list_widget, checked, only_visible=False) — выставляет
  Checked/Unchecked всем элементам; only_visible=True пропускает item.isHidden()
  (используется для «Выбрать все» после фильтра/поиска).
- invert_checked(list_widget, only_visible=False) — инвертирует состояние;
  only_visible=True пропускает скрытые элементы (используется для
  «Инвертировать»).
- «Снять все» (deselect) во всех трёх местах и раньше, и теперь работает по
  ВСЕМ элементам без исключения (only_visible=False) — это было идентичным
  поведением обеих исходных копий (chapter_selection_dialog и versioning),
  осознанно сохранено как есть.
- Обе функции оборачивают проход в setUpdatesEnabled(False)/True — чистая
  перф-оптимизация (не меняет наблюдаемое поведение checkState()/itemChanged).

РЕАЛЬНЫЙ БАГ, исправленный при канонизации: ranobelib/main_window.py имеет
собственный QLineEdit-поиск (`search_input` → `_filter_chapters`), который
скрывает элементы chapters_list_widget через setHidden — то есть, вопреки
описанию в задании кластера («в ranobelib фильтрации нет вовсе»), фильтр
ЕСТЬ (main_window.py:1157, подключён на строке 551). Старые _select_all и
_invert_selection в ranobelib НЕ проверяли item.isHidden() — то есть после
фильтрации по подстроке кнопки «Выбрать все»/«Инвертировать» и Ctrl+A
затрагивали и скрытые (не соответствующие фильтру) главы. Это расходится с
корректным поведением тех же операций в chapter_selection_dialog.py и
versioning.py (которые isHidden() уважают). Канонизация ranobelib теперь
вызывает set_checked_all(..., only_visible=True) / invert_checked(...,
only_visible=True), приводя её в соответствие с остальными двумя копиями и
устраняя баг. «Снять все» в ranobelib и раньше игнорировало фильтр
(only_visible=False) — оставлено как есть, симметрично двум другим местам.

(a) Характеризационные тесты канонических функций на граничных случаях.
(b) Маршрутизационные тесты: _select_all/_deselect_all/_invert_selection в
    каждом из трёх мест обязаны реально вызывать канонические функции —
    патчим их и проверяем факт/аргументы вызова. До рефакторинга каждое
    место содержит собственную копию и патч её не перехватывает — RED.
    После рефакторинга — GREEN.
"""
import os
import sys
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets  # noqa: E402
from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QListWidget, QListWidgetItem  # noqa: E402

from gemini_translator.ui.dialogs.chapter_selection_dialog import (  # noqa: E402
    ChapterSelectionDialog,
    set_checked_all,
    invert_checked,
)
from gemini_translator.ui.dialogs.glossary_dialogs.versioning import (  # noqa: E402
    ChapterSelectorWidget,
)

TESTS_DIR = os.path.dirname(__file__)
PROJECT_ROOT = os.path.dirname(TESTS_DIR)
RANOBELIB_DIR = os.path.join(PROJECT_ROOT, "ranobelib")

if RANOBELIB_DIR not in sys.path:
    sys.path.insert(0, RANOBELIB_DIR)

_APP_REF = None


def _ensure_qapp():
    global _APP_REF
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    _APP_REF = app  # держим ссылку — иначе PyQt6 может собрать QApplication
    return app


def _make_list(states, hidden=None):
    """count() элементов с начальным checkState и опциональной isHidden()-маской."""
    _ensure_qapp()
    widget = QListWidget()
    hidden = hidden or set()
    for i, state in enumerate(states):
        item = QListWidgetItem(f"item-{i}")
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(state)
        widget.addItem(item)
        if i in hidden:
            item.setHidden(True)
    return widget


CANONICAL_MODULE = "gemini_translator.ui.dialogs.chapter_selection_dialog"


# ─── (a) Характеризационные тесты канонических функций ───────────────────

def test_set_checked_all_checked_true_sets_all_visible_and_hidden_by_default():
    widget = _make_list(
        [Qt.CheckState.Unchecked, Qt.CheckState.Unchecked, Qt.CheckState.Unchecked],
        hidden={1},
    )
    set_checked_all(widget, True)
    states = [widget.item(i).checkState() for i in range(widget.count())]
    assert states == [Qt.CheckState.Checked] * 3


def test_set_checked_all_checked_true_only_visible_skips_hidden():
    widget = _make_list(
        [Qt.CheckState.Unchecked, Qt.CheckState.Unchecked, Qt.CheckState.Unchecked],
        hidden={1},
    )
    set_checked_all(widget, True, only_visible=True)
    states = [widget.item(i).checkState() for i in range(widget.count())]
    assert states == [Qt.CheckState.Checked, Qt.CheckState.Unchecked, Qt.CheckState.Checked]


def test_set_checked_all_checked_false_clears_hidden_too_by_default():
    """«Снять все» — деселект должен сбрасывать ВСЕ элементы, включая скрытые фильтром."""
    widget = _make_list(
        [Qt.CheckState.Checked, Qt.CheckState.Checked, Qt.CheckState.Checked],
        hidden={1},
    )
    set_checked_all(widget, False, only_visible=False)
    states = [widget.item(i).checkState() for i in range(widget.count())]
    assert states == [Qt.CheckState.Unchecked] * 3


def test_invert_checked_flips_all_by_default():
    widget = _make_list(
        [Qt.CheckState.Checked, Qt.CheckState.Unchecked, Qt.CheckState.Checked],
        hidden={1},
    )
    invert_checked(widget)
    states = [widget.item(i).checkState() for i in range(widget.count())]
    assert states == [Qt.CheckState.Unchecked, Qt.CheckState.Checked, Qt.CheckState.Unchecked]


def test_invert_checked_only_visible_skips_hidden():
    widget = _make_list(
        [Qt.CheckState.Checked, Qt.CheckState.Unchecked, Qt.CheckState.Checked],
        hidden={1},
    )
    invert_checked(widget, only_visible=True)
    states = [widget.item(i).checkState() for i in range(widget.count())]
    assert states == [Qt.CheckState.Unchecked, Qt.CheckState.Unchecked, Qt.CheckState.Unchecked]


def test_set_checked_all_empty_list_is_noop():
    widget = _make_list([])
    set_checked_all(widget, True, only_visible=True)  # не должно упасть
    assert widget.count() == 0


# ─── (b) Маршрутизационные тесты ──────────────────────────────────────────

def test_chapter_selection_dialog_select_all_routes_through_canonical():
    _ensure_qapp()
    chapters = [{"name": "Глава 1", "path": "Text/ch1.xhtml", "content": ""}]
    dialog = ChapterSelectionDialog(chapters)
    try:
        with patch(f"{CANONICAL_MODULE}.set_checked_all") as mocked:
            dialog._select_all()
            mocked.assert_called_once_with(dialog.chapter_list, True, only_visible=True)
    finally:
        dialog.deleteLater()


def test_chapter_selection_dialog_deselect_all_routes_through_canonical():
    _ensure_qapp()
    chapters = [{"name": "Глава 1", "path": "Text/ch1.xhtml", "content": ""}]
    dialog = ChapterSelectionDialog(chapters)
    try:
        with patch(f"{CANONICAL_MODULE}.set_checked_all") as mocked:
            dialog._deselect_all()
            mocked.assert_called_once_with(dialog.chapter_list, False, only_visible=False)
    finally:
        dialog.deleteLater()


def test_chapter_selection_dialog_invert_routes_through_canonical():
    _ensure_qapp()
    chapters = [{"name": "Глава 1", "path": "Text/ch1.xhtml", "content": ""}]
    dialog = ChapterSelectionDialog(chapters)
    try:
        with patch(f"{CANONICAL_MODULE}.invert_checked") as mocked:
            dialog._invert_selection()
            mocked.assert_called_once_with(dialog.chapter_list, only_visible=True)
    finally:
        dialog.deleteLater()


def test_versioning_select_all_routes_through_canonical(tmp_path):
    _ensure_qapp()
    widget = ChapterSelectorWidget(epub_path=str(tmp_path / "missing.epub"))
    try:
        with patch(f"{CANONICAL_MODULE}.set_checked_all") as mocked:
            widget._select_all()
            mocked.assert_called_once_with(widget.list_widget, True, only_visible=True)
    finally:
        widget.deleteLater()


def test_versioning_deselect_all_routes_through_canonical(tmp_path):
    _ensure_qapp()
    widget = ChapterSelectorWidget(epub_path=str(tmp_path / "missing.epub"))
    try:
        with patch(f"{CANONICAL_MODULE}.set_checked_all") as mocked:
            widget._deselect_all()
            mocked.assert_called_once_with(widget.list_widget, False, only_visible=False)
    finally:
        widget.deleteLater()


def test_ranobelib_select_all_routes_through_canonical():
    import main_window as ranobelib_main_window

    class _Harness:
        _select_all = ranobelib_main_window.RanobeUploaderApp._select_all

        def __init__(self, list_widget):
            self.chapters_list_widget = list_widget

    _ensure_qapp()
    widget = _make_list([Qt.CheckState.Unchecked, Qt.CheckState.Unchecked], hidden={1})
    harness = _Harness(widget)
    with patch(f"{CANONICAL_MODULE}.set_checked_all") as mocked:
        harness._select_all()
        mocked.assert_called_once_with(widget, True, only_visible=True)


def test_ranobelib_deselect_all_routes_through_canonical():
    import main_window as ranobelib_main_window

    class _Harness:
        _deselect_all = ranobelib_main_window.RanobeUploaderApp._deselect_all

        def __init__(self, list_widget):
            self.chapters_list_widget = list_widget

    _ensure_qapp()
    widget = _make_list([Qt.CheckState.Checked, Qt.CheckState.Checked], hidden={1})
    harness = _Harness(widget)
    with patch(f"{CANONICAL_MODULE}.set_checked_all") as mocked:
        harness._deselect_all()
        mocked.assert_called_once_with(widget, False, only_visible=False)


def test_ranobelib_invert_selection_routes_through_canonical():
    import main_window as ranobelib_main_window

    class _Harness:
        _invert_selection = ranobelib_main_window.RanobeUploaderApp._invert_selection

        def __init__(self, list_widget):
            self.chapters_list_widget = list_widget

    _ensure_qapp()
    widget = _make_list([Qt.CheckState.Checked, Qt.CheckState.Unchecked], hidden={1})
    harness = _Harness(widget)
    with patch(f"{CANONICAL_MODULE}.invert_checked") as mocked:
        harness._invert_selection()
        mocked.assert_called_once_with(widget, only_visible=True)


def test_ranobelib_filter_still_hides_items_select_all_then_respects_it():
    """Регрессия к найденному багу: после фильтра «Выбрать все» не должно
    затрагивать скрытые (несовпадающие с фильтром) главы."""
    _ensure_qapp()
    widget = _make_list(
        [Qt.CheckState.Unchecked, Qt.CheckState.Unchecked, Qt.CheckState.Unchecked],
        hidden={1},
    )
    set_checked_all(widget, True, only_visible=True)
    assert widget.item(0).checkState() == Qt.CheckState.Checked
    assert widget.item(1).checkState() == Qt.CheckState.Unchecked  # скрыт фильтром
    assert widget.item(2).checkState() == Qt.CheckState.Checked
