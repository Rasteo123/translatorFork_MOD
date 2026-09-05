"""
Регресс для ui-dialogs-validation/logic/1-code-view-edit-wrong-row.

update_comparison_view/toggle_code_view выбирают строку для отображения в
редакторе кода через `selected_items[0].row()`, а on_text_edited/
on_selection_changed определяли строку через `list(set(item.row() ...))[0]` —
порядок обхода множества CPython, который не обязан совпадать с порядком
selectedItems(). При ctrl-выделении строк в порядке «сначала нижняя, потом
верхняя» редактор показывает одну главу, а правка улетает в results_data
другой главы.

Тест привязывает НАСТОЯЩИЕ тела методов TranslationValidatorPage
(toggle_code_view, update_comparison_view, on_text_edited,
on_selection_changed) к минимальному объекту с реальным QTableWidget/QTextEdit
(offscreen Qt), без сети и без реальных настроек пользователя.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QItemSelectionModel
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
)

from gemini_translator.ui.dialogs.validation import TranslationValidatorPage as P


_APP = QApplication.instance() or QApplication([])


def _make_app():
    # Держим ссылку на QApplication на уровне модуля: без неё singleton
    # уничтожается сборщиком мусора сразу после возврата из функции, и
    # следующий QWidget падает с "Must construct a QApplication before
    # a QWidget".
    return _APP


class _Harness:
    """Минимальный объект с реальными телами методов TranslationValidatorPage."""

    on_text_edited = P.on_text_edited
    toggle_code_view = P.toggle_code_view
    update_comparison_view = P.update_comparison_view
    on_selection_changed = P.on_selection_changed
    _apply_highlighting = P._apply_highlighting

    def __init__(self, n=6):
        self.is_code_view = False
        self.is_comparing_validated = False
        self.project_manager = None
        self.results_data = {}
        for r in range(n):
            html = f"<p>ГЛАВА {r} ПЕРЕВОД</p>"
            self.results_data[r] = {
                "path": f"ch{r}.html",
                "internal_html_path": f"ch{r}.xhtml",
                "translated_html": html,
                "original_html": f"<p>ORIG {r}</p>",
                "untranslated_words": [],
                "is_edited": False,
            }

        self.table_results = QTableWidget(n, 4)
        self.table_results.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table_results.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        for r in range(n):
            for c in range(4):
                self.table_results.setItem(r, c, QTableWidgetItem(""))

        self.view_original = QTextEdit()
        self.view_translated = QTextEdit()
        self.view_translated.textChanged.connect(self.on_text_edited)
        self.btn_toggle_code_view = QPushButton()
        self.btn_toggle_compare = QPushButton()
        self.btn_save_changes = QPushButton()
        self.btn_prev_item = QPushButton()
        self.btn_next_item = QPushButton()
        self.regex_edit = QLineEdit()
        self.check_case_sensitive = QCheckBox()
        self.lbl_status = QLabel()
        self.table_results.itemSelectionChanged.connect(lambda: self.on_selection_changed())

    # --- заглушки дисковых загрузчиков контента ---
    def _ensure_row_translated_html_loaded(self, row):
        return self.results_data[row]["translated_html"]

    def _ensure_row_original_html_loaded(self, row):
        return self.results_data[row]["original_html"]

    def _ensure_row_validated_content_loaded(self, row):
        return None

    def _update_highlighters(self):
        pass

    def _update_translation_find_replace_state(self):
        pass

    def update_row_color(self, row, kind):
        pass


def _ctrl_select(table, first_row, second_row):
    """Имитирует ctrl-клик: сначала first_row, затем добавление second_row."""
    table.selectRow(first_row)
    table.selectionModel().select(
        table.model().index(second_row, 0),
        QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
    )


def test_ctrl_select_lower_then_upper_diverges_in_row_helpers():
    """
    Санити-проверка механизма: подтверждаем, что selectedItems()[0].row() и
    list(set(...))[0] действительно расходятся для выбранной пары строк —
    иначе последующая проверка ничего не доказывает.
    """
    _make_app()
    h = _Harness()
    _ctrl_select(h.table_results, 3, 1)

    items_row = h.table_results.selectedItems()[0].row()
    set_row = list(set(item.row() for item in h.table_results.selectedItems()))[0]

    assert items_row == 3
    assert set_row != items_row, (
        "Стенд не воспроизводит расхождение порядков — проверьте выбор строк"
    )


def test_edit_lands_in_row_shown_by_editor_not_in_other_row():
    """
    Основной регресс: после ctrl-выделения (сначала строка 3, потом строка 1)
    редактор кода показывает главу 3. Правка, введённая в редакторе, должна
    попасть в results_data[3], а НЕ в results_data[1] (главу 1 трогать нельзя).
    """
    _make_app()
    h = _Harness()
    _ctrl_select(h.table_results, 3, 1)

    h.toggle_code_view()
    assert h.is_code_view is True
    shown_row = h.table_results.selectedItems()[0].row()
    assert h.view_translated.toPlainText() == h.results_data[shown_row]["translated_html"]

    # Правим текст, реально показанный в редакторе.
    cursor = h.view_translated.textCursor()
    cursor.movePosition(cursor.MoveOperation.End)
    h.view_translated.setTextCursor(cursor)
    h.view_translated.insertPlainText("X")

    # Правка обязана попасть в ту же строку, что отображал редактор.
    assert h.results_data[shown_row]["is_edited"] is True
    assert h.results_data[shown_row]["translated_html"].endswith("X")

    # Другая выделенная строка не должна быть тронута.
    other_row = 1 if shown_row != 1 else 3
    assert h.results_data[other_row]["is_edited"] is False
    assert h.results_data[other_row]["translated_html"] == f"<p>ГЛАВА {other_row} ПЕРЕВОД</p>"
