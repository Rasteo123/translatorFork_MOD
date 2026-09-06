"""
cluster-58: логика «скопировать текст ошибки в буфер обмена и вернуть
подпись кнопки через 2с» была продублирована между main.py
(run_emergency_viewer) и os_patch.py (_patched_qmessagebox_critical).

Канонической реализацией стала os_patch.attach_copy_feedback(...):
- (а) характеризационные тесты фиксируют её поведение (крайние случаи,
  которые различали копии до рефакторинга: дефолтная подпись idle,
  таймер 2000мс, повторные клики не плодят новые QTimer);
- (б) тест-маршрутизация проверяет, что оба бывших места вызова
  (main.run_emergency_viewer, os_patch._patched_qmessagebox_critical)
  реально идут через attach_copy_feedback, а не через свою копию.
"""
import sys

import pytest
from PyQt6 import QtCore, QtWidgets
from PyQt6.QtTest import QTest

import os_patch
# main импортируется на уровне модуля, ДО любых monkeypatch: main.py на
# импорте определяет `class ApplicationWithContext(QtWidgets.QApplication)`,
# и если QtWidgets.QApplication к этому моменту подменён на фейк, наследование
# падает с TypeError.
import main

# Держим ссылку на QApplication на уровне модуля: без неё singleton
# может быть собран сборщиком мусора между тестами, и следующий
# QWidget падает с "Must construct a QApplication before a QWidget"
# (или жёстче — Fatal Python error: Aborted).
_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _qapp():
    return _APP


# ---------------------------------------------------------------------------
# (а) Характеризационные тесты канонической реализации
# ---------------------------------------------------------------------------

def test_attach_copy_feedback_copies_text_and_shows_confirmation():
    _qapp()
    button = QtWidgets.QPushButton("Скопировать ошибку")
    owner = QtCore.QObject()

    os_patch.attach_copy_feedback(button, owner, lambda: "текст ошибки")
    button.click()

    assert QtWidgets.QApplication.clipboard().text() == "текст ошибки"
    assert button.text() == "Скопировано!"
    assert button.isEnabled() is False


def test_attach_copy_feedback_idle_label_defaults_to_original_button_text():
    _qapp()
    button = QtWidgets.QPushButton("Моя подпись")
    owner = QtCore.QObject()

    os_patch.attach_copy_feedback(button, owner, lambda: "x")
    button.click()
    QTest.qWait(50)

    reset_timer = getattr(owner, "_copy_reset_timer")
    reset_timer.timeout.emit()  # форсируем срабатывание, не дожидаясь 2с

    assert button.text() == "Моя подпись"
    assert button.isEnabled() is True


def test_attach_copy_feedback_resets_after_duration():
    _qapp()
    button = QtWidgets.QPushButton("Скопировать ошибку")
    owner = QtCore.QObject()

    os_patch.attach_copy_feedback(button, owner, lambda: "x", duration_ms=30)
    button.click()
    assert button.isEnabled() is False

    QTest.qWait(200)

    assert button.text() == "Скопировать ошибку"
    assert button.isEnabled() is True


def test_attach_copy_feedback_reuses_cached_timer_on_repeated_clicks():
    _qapp()
    button = QtWidgets.QPushButton("Скопировать ошибку")
    owner = QtCore.QObject()

    os_patch.attach_copy_feedback(button, owner, lambda: "x", duration_ms=5000)
    button.click()
    first_timer = getattr(owner, "_copy_reset_timer", None)
    assert first_timer is not None

    button.setEnabled(True)  # имитируем повторный клик до истечения таймера
    button.click()
    second_timer = getattr(owner, "_copy_reset_timer", None)

    assert second_timer is first_timer


def test_attach_copy_feedback_custom_labels_and_duration():
    _qapp()
    button = QtWidgets.QPushButton("Скопировать всё")
    owner = QtCore.QObject()

    os_patch.attach_copy_feedback(
        button,
        owner,
        lambda: "y",
        copied_label="Готово!",
        idle_label="Скопировать всё ещё раз",
        duration_ms=20,
    )
    button.click()
    assert button.text() == "Готово!"

    QTest.qWait(150)
    assert button.text() == "Скопировать всё ещё раз"
    assert button.isEnabled() is True


# ---------------------------------------------------------------------------
# (б) Тест-маршрутизация: бывшие места вызова обязаны идти через
# os_patch.attach_copy_feedback, а не через собственную копию логики.
# ---------------------------------------------------------------------------

def test_run_emergency_viewer_routes_through_attach_copy_feedback(monkeypatch):
    """RED до рефакторинга: main.py использует свою копию логики и не
    зовёт os_patch.attach_copy_feedback вовсе."""
    app = _qapp()

    calls = []

    def fake_attach(button, owner, get_text, **kwargs):
        calls.append((button, owner, get_text, kwargs))
        return lambda: None

    monkeypatch.setattr(os_patch, "attach_copy_feedback", fake_attach, raising=False)
    # install_window_title_branding патчит QWidget.setWindowTitle на уровне
    # класса и не снимается — иначе все последующие тесты в прогоне получают
    # заголовки с префиксом «translatorFork_MOD - ». Брендинг здесь не при чём.
    monkeypatch.setattr(main, "install_window_title_branding", lambda app=None: None)
    monkeypatch.setattr(sys, "argv", ["main.py", "--emergency-viewer"])
    monkeypatch.setattr(QtWidgets.QDialog, "exec", lambda self: 0)
    monkeypatch.setattr(sys, "exit", lambda code=0: (_ for _ in ()).throw(SystemExit(code)))

    # run_emergency_viewer создаёt QApplication(sys.argv) безусловно; в
    # процессе тестов уже есть живой экземпляр — второй создать нельзя,
    # поэтому подменяем конструктор на возврат существующего instance.
    # Патч живёт только внутри `with`, чтобы к моменту teardown pytest-qt
    # (который тоже дёргает QtWidgets.QApplication.instance()) атрибут уже
    # был восстановлен.
    with monkeypatch.context() as m:
        m.setattr(QtWidgets, "QApplication", lambda argv: app)
        with pytest.raises(SystemExit):
            main.run_emergency_viewer()

    assert len(calls) == 1, (
        "run_emergency_viewer должен настраивать кнопку копирования через "
        "os_patch.attach_copy_feedback, а не через собственную копию логики"
    )
    button, owner, get_text, kwargs = calls[0]
    assert isinstance(button, QtWidgets.QPushButton)
    assert callable(get_text)


def test_patched_qmessagebox_critical_routes_through_attach_copy_feedback(monkeypatch):
    """RED до рефакторинга: os_patch.py использует свою копию логики
    внутри _patched_qmessagebox_critical вместо общей функции."""
    _qapp()

    calls = []

    def fake_attach(button, owner, get_text, **kwargs):
        calls.append((button, owner, get_text, kwargs))
        return lambda: None

    monkeypatch.setattr(os_patch, "attach_copy_feedback", fake_attach, raising=False)
    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", lambda self: 0)
    # Сбрасываем статический счётчик "Error Storm" между тестами.
    if hasattr(os_patch._patched_qmessagebox_critical, "active_count"):
        os_patch._patched_qmessagebox_critical.active_count = 0

    os_patch._patched_qmessagebox_critical(None, "Заголовок", "Текст ошибки")

    assert len(calls) == 1, (
        "_patched_qmessagebox_critical должен настраивать кнопку копирования "
        "через os_patch.attach_copy_feedback, а не через собственную копию логики"
    )
    button, owner, get_text, kwargs = calls[0]
    assert isinstance(button, QtWidgets.QAbstractButton)
    assert get_text() == "Текст ошибки"
