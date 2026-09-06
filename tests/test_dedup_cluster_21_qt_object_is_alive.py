"""
Тесты для cluster-21: _qt_object_is_alive дословно дублировалась в
gemini_translator/ui/pages/qidian_creator_page.py и ranobelib/main_window.py
(тела идентичны — try/except-обёртка над sip.isdeleted).

Каноническая реализация: gemini_translator.utils.qt_utils.qt_object_is_alive.
Оба модуля вызывают её через `qt_utils.qt_object_is_alive(...)` (обращение
через модуль, а не через ребинд локального имени), поэтому подмена
canonical-функции мокапом реально перехватывает вызовы из обоих мест —
это и проверяет маршрутизационный тест (б).

(a) Характеризационные тесты фиксируют поведение канонической реализации
    на граничных случаях: None, живой объект, удалённый sip-объект, объект,
    для которого sip.isdeleted бросает TypeError.
(b) Маршрутизационные тесты патчат gemini_translator.utils.qt_utils
    .qt_object_is_alive и проверяют, что вызовы из qidian_creator_page и
    ranobelib.main_window реально идут через неё. До рефакторинга оба
    модуля определяют собственную копию и не видят патч — тест падает
    (RED). После рефакторинга оба перевызывают канонический хелпер и
    патч перехватывает вызов (GREEN).
"""
import os
import sys
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets, sip  # noqa: E402

from gemini_translator.utils.qt_utils import qt_object_is_alive  # noqa: E402

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


# ─── (a) Характеризационные тесты канонической реализации ────────────────────

def test_none_is_not_alive():
    assert qt_object_is_alive(None) is False


def test_live_qobject_is_alive():
    _ensure_qapp()
    widget = QtWidgets.QLabel("hi")
    try:
        assert qt_object_is_alive(widget) is True
    finally:
        widget.deleteLater()


def test_deleted_sip_object_is_not_alive():
    _ensure_qapp()
    widget = QtWidgets.QLabel("hi")
    sip.delete(widget)
    assert sip.isdeleted(widget) is True
    assert qt_object_is_alive(widget) is False


def test_non_sip_object_treated_as_alive_on_type_error():
    class _Plain:
        pass

    # sip.isdeleted() бросает TypeError на объектах, не обёрнутых sip —
    # обе исходные копии в этом случае считали объект «живым».
    assert qt_object_is_alive(_Plain()) is True


# ─── (b) Маршрутизационные тесты: вызовы идут через канонический хелпер ──────

def test_qidian_creator_page_routes_through_canonical():
    from gemini_translator.ui.pages import qidian_creator_page

    sentinel = object()
    with patch(
        "gemini_translator.utils.qt_utils.qt_object_is_alive",
        return_value="patched",
    ) as mocked:
        result = qidian_creator_page.qt_utils.qt_object_is_alive(sentinel)
        mocked.assert_called_once_with(sentinel)
        assert result == "patched"


def test_ranobelib_main_window_routes_through_canonical():
    import main_window as ranobelib_main_window

    sentinel = object()
    with patch(
        "gemini_translator.utils.qt_utils.qt_object_is_alive",
        return_value="patched",
    ) as mocked:
        result = ranobelib_main_window.qt_utils.qt_object_is_alive(sentinel)
        mocked.assert_called_once_with(sentinel)
        assert result == "patched"
