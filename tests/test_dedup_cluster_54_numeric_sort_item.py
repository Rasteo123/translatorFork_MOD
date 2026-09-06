# -*- coding: utf-8 -*-
"""cluster-54: три копии числовой сортировки QTableWidgetItem.__lt__ сведены
к единой gemini_translator.ui.widgets.table_utils.NumericSortItem с key_fn.

(а) Характеризационные тесты фиксируют поведение каждого бывшего формата
    ячейки (симв./запятые, "число | доп.инфо", путь главы через
    extract_number_from_path) на канонической реализации.
(б) Тесты-маршрутизация подменяют NumericSortItem.__lt__ и проверяют, что
    все три бывших места создания элементов используют именно его — эти
    тесты обязаны падать до рефакторинга (собственная копия __lt__ у
    каждого места) и проходить после.
"""
import os
import subprocess
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication, QTableWidgetItem

from gemini_translator.ui.widgets.table_utils import NumericSortItem


@pytest.fixture(scope="module", autouse=True)
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


# ---------------------------------------------------------------------------
# (а) Характеризационные тесты канонической реализации
# ---------------------------------------------------------------------------

class TestNumericSortItemDefaultKey:
    """Формат бывшего SortableTableWidgetItem (txt_importer.py): пробелы,
    запятые-разделители тысяч и суффикс 'симв.' — всё отбрасывается перед
    float()."""

    def test_strips_comma_thousands_separator(self):
        a = NumericSortItem("1,234")
        b = NumericSortItem("999")
        assert not (a < b)
        assert b < a

    def test_strips_space_thousands_separator_and_suffix(self):
        a = NumericSortItem("1 234 симв.")
        b = NumericSortItem("500 симв.")
        assert b < a
        assert not (a < b)

    def test_plain_integer_text(self):
        a = NumericSortItem("2")
        b = NumericSortItem("10")
        assert a < b

    def test_falls_back_to_text_compare_on_non_numeric(self):
        a = NumericSortItem("abc")
        b = NumericSortItem("abd")
        # float() бросает ValueError -> откат на QTableWidgetItem.__lt__ (текст)
        assert a < b


class TestNumericSortItemPipeKey:
    """Формат бывшего NumericTableWidgetItem (validation.py): 'число | доп.инфо'."""

    @staticmethod
    def _pipe_key(item):
        return float(item.text().split('|')[0].strip())

    def test_compares_numeric_prefix(self):
        a = NumericSortItem("150 | доп.инфо", key_fn=self._pipe_key)
        b = NumericSortItem("42 | другое", key_fn=self._pipe_key)
        assert b < a
        assert not (a < b)

    def test_falls_back_on_bad_prefix(self):
        a = NumericSortItem("n/a | x", key_fn=self._pipe_key)
        b = NumericSortItem("n/b | y", key_fn=self._pipe_key)
        assert a < b  # текстовое сравнение "n/a" < "n/b"


class TestNumericSortItemLtRobustToBypassedInit:
    """Ревью cluster-54 (minor): __lt__ раньше был stateless, поэтому объект,
    созданный в обход __init__ (Qt clone()/prototype), не падал. Новый
    __lt__ читает self._key_fn/_require_same_type — они обязаны быть
    доступны как атрибуты класса по умолчанию, а не только после __init__,
    иначе сравнение бросает AttributeError вместо отката на текст."""

    def test_lt_does_not_raise_when_constructed_via_new_bypassing_init(self):
        a = NumericSortItem.__new__(NumericSortItem)
        QTableWidgetItem.__init__(a, "AAA")  # инициализируем базовый Qt-объект, минуя NumericSortItem.__init__
        b = NumericSortItem("BBB")
        # Не должно бросать AttributeError на отсутствующих _key_fn/_require_same_type;
        # a."AAA" не парсится как число -> откат на текстовое сравнение.
        assert (a < b) == ("AAA" < "BBB")


class TestNumericSortItemRequireSameTypeDeclaredClass:
    """Ревью cluster-54 (minor): require_same_type сравнивал(о) с type(self),
    а не с явно объявленным классом (как оригинальный
    isinstance(other, SortableChapterItem)). При появлении подкласса это
    асимметрично: sub.__lt__(base) откатывается на текст, а base.__lt__(sub)
    — нет, хотя должны давать согласованный числовой результат."""

    def test_require_same_type_is_symmetric_via_explicit_same_type_as(self):
        # Один общий key_fn (как extract_number_from_path в реальном коде) —
        # он читает *переданный ему* элемент, а не только self, поэтому
        # должен давать согласованный числовой результат в обе стороны.
        numeric_key_by_text = {"AAA": 2.0, "ZZZ": 1.0}.get

        class ChapterKind(NumericSortItem):
            pass

        base = NumericSortItem(
            "AAA",
            key_fn=lambda it: numeric_key_by_text(it.text()),
            require_same_type=True,
            same_type_as=NumericSortItem,
        )
        sub = ChapterKind(
            "ZZZ",
            key_fn=lambda it: numeric_key_by_text(it.text()),
            require_same_type=True,
            same_type_as=NumericSortItem,
        )

        # 2.0 < 1.0 является False
        assert not (base < sub)
        # 1.0 < 2.0 является True — направление sub < base обязано быть
        # согласовано с base < sub (симметрично), что требует проверки
        # isinstance против явного same_type_as, а не против type(self)
        # (со старым type(self) это направление тихо откатывалось бы на
        # текстовое "ZZZ" < "AAA" -> False, что противоречило бы числам).
        assert sub < base


class TestNumericSortItemRequireSameType:
    """Формат бывшего SortableChapterItem (validation.py): числовой ключ
    берётся не из текста ячейки, а из внешнего атрибута (internal_path) через
    extract_number_from_path; при сравнении с ячейкой другого типа —
    откат на текстовое сравнение, а не попытка выцепить число из чужого
    объекта."""

    @staticmethod
    def _path_key(item):
        from gemini_translator.utils.epub_tools import extract_number_from_path
        return extract_number_from_path(item)

    def _chapter_item(self, text, path):
        item = NumericSortItem(text, key_fn=self._path_key, require_same_type=True)
        item.internal_path = path
        return item

    def test_sorts_by_internal_path_not_display_text(self):
        # Текст ячейки специально "перепутан" с порядком путей, чтобы
        # доказать, что сравнение идёт по internal_path, а не по self.text().
        a = self._chapter_item("Z-глава", "OEBPS/chap_002.xhtml")
        b = self._chapter_item("A-глава", "OEBPS/chap_010.xhtml")
        assert a < b  # 2 < 10, несмотря на "Z" > "A" в тексте

    def test_does_not_apply_numeric_key_against_other_item_type(self):
        chapter = self._chapter_item("глава", "OEBPS/chap_005.xhtml")
        plain = QTableWidgetItem("aaaa")  # НЕ NumericSortItem с require_same_type
        # Ожидаем откат на QTableWidgetItem.__lt__ (текст), как в оригинале
        # (isinstance(other, SortableChapterItem) было False).
        assert chapter.__lt__(plain) == QTableWidgetItem.__lt__(chapter, plain)


# ---------------------------------------------------------------------------
# (б) Маршрутизация: все три бывших места создания идут через NumericSortItem
# ---------------------------------------------------------------------------

def test_txt_importer_toc_table_routes_through_numeric_sort_item(monkeypatch):
    """_refresh_toc_table (бывший источник SortableTableWidgetItem) должен
    создавать ячейки размера/индекса/строки как NumericSortItem."""
    from types import SimpleNamespace
    from PyQt6.QtWidgets import QTableWidget
    from gemini_translator.utils import txt_importer

    calls = []
    original_lt = NumericSortItem.__lt__

    def spy_lt(self, other):
        calls.append(self)
        return original_lt(self, other)

    monkeypatch.setattr(NumericSortItem, "__lt__", spy_lt, raising=True)

    dialog = txt_importer.TxtImportWizardDialog.__new__(txt_importer.TxtImportWizardDialog)
    dialog.toc_table = QTableWidget()
    dialog.toc_table.setColumnCount(4)
    dialog.structure_data = [
        {"line_idx": 0, "title": "Глава 1", "char_idx": 0},
        {"line_idx": 5, "title": "Глава 2", "char_idx": 120},
    ]
    dialog.analyzer = SimpleNamespace(
        lines=["x"] * 10,
        line_lengths=[10] * 10,
    )

    dialog._refresh_toc_table()

    size_item = dialog.toc_table.item(0, 1)
    assert isinstance(size_item, NumericSortItem)
    _ = size_item < dialog.toc_table.item(1, 1)
    assert calls, "Ячейки таблицы импорта TXT должны использовать NumericSortItem.__lt__"


def test_validation_numeric_table_item_routes_through_numeric_sort_item(monkeypatch):
    from gemini_translator.ui.dialogs import validation

    calls = []
    original_lt = NumericSortItem.__lt__

    def spy_lt(self, other):
        calls.append(self)
        return original_lt(self, other)

    monkeypatch.setattr(NumericSortItem, "__lt__", spy_lt, raising=True)

    a = validation.NumericTableWidgetItem("150 | доп.инфо")
    b = validation.NumericTableWidgetItem("42 | другое")
    assert isinstance(a, NumericSortItem)
    _ = a < b
    assert calls, "NumericTableWidgetItem должен использовать NumericSortItem.__lt__"


def test_validation_chapter_item_routes_through_numeric_sort_item(monkeypatch):
    from gemini_translator.ui.dialogs import validation

    calls = []
    original_lt = NumericSortItem.__lt__

    def spy_lt(self, other):
        calls.append(self)
        return original_lt(self, other)

    monkeypatch.setattr(NumericSortItem, "__lt__", spy_lt, raising=True)

    a = validation.SortableChapterItem("Глава 2", "OEBPS/chap_002.xhtml")
    b = validation.SortableChapterItem("Глава 10", "OEBPS/chap_010.xhtml")
    assert isinstance(a, NumericSortItem)
    assert a < b
    assert calls, "SortableChapterItem должен использовать NumericSortItem.__lt__"


def test_txt_importer_does_not_import_ui_widgets_package_at_module_level():
    """Ревью cluster-54 (major): gemini_translator/utils/txt_importer.py —
    модуль слоя utils — не должен на верхнем уровне импортировать
    gemini_translator.ui.widgets (жадный __init__.py тянет 14
    виджет-модулей, ~69 лишних модулей при голом импорте txt_importer).
    NumericSortItem нужен только внутри _refresh_toc_table — импорт должен
    быть локальным для этой функции."""
    script = (
        "import sys\n"
        "import gemini_translator.utils.txt_importer\n"
        "print('WIDGETS_PKG_LOADED=' + str('gemini_translator.ui.widgets' in sys.modules))\n"
    )
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "WIDGETS_PKG_LOADED=False" in result.stdout, (
        "import gemini_translator.utils.txt_importer не должен тянуть за собой "
        "gemini_translator.ui.widgets на верхнем уровне (utils не должен "
        "импортировать ui-слой при импорте модуля).\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
