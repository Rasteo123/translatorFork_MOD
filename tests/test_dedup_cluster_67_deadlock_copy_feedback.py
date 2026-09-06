"""
cluster-67: DeadlockNotifier._on_show (os_patch.py) реализовывал
"скопировать текст в буфер + сбросить подпись/enabled кнопки через 2с"
собственной локальной функцией copy_action, дублируя каноническую
os_patch.attach_copy_feedback (уже выделена в cluster-58 — см.
tests/test_dedup_cluster_58_copy_feedback.py, используется в
main.run_emergency_viewer и os_patch._patched_qmessagebox_critical).

Разошедшийся момент (divergence): copy_action в DeadlockNotifier делал
только setText(text) + copy_btn.setText("Скопировано!") +
copy_btn.setEnabled(False) — БЕЗ какого-либо таймера сброса. Кнопка
оставалась задизейбленной и с подписью "Скопировано!" до конца жизни
диалога, в отличие от двух других мест, где подпись/enabled
возвращаются через 2с.

(а) характеризационный тест фиксирует итоговое (исправленное) поведение
    : клик копирует текст, временно блокирует кнопку и возвращает её в
    исходное состояние через duration_ms;
(б) тест-маршрутизация проверяет, что _on_show реально вызывает
    os_patch.attach_copy_feedback, а не собственную копию логики — этот
    тест обязан быть RED до рефакторинга (copy_action был локальным,
    attach_copy_feedback вообще не вызывался) и GREEN после.
"""
from PyQt6 import QtWidgets
from PyQt6.QtTest import QTest

import os_patch

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _qapp():
    return _APP


def _make_notifier(monkeypatch):
    _qapp()
    # _on_show вызывает msg.exec() синхронно — в тестах диалог не должен
    # реально блокировать поток.
    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", lambda self: 0)
    return os_patch.DeadlockNotifier()


# ---------------------------------------------------------------------------
# (б) Тест-маршрутизация: _on_show обязан идти через
# os_patch.attach_copy_feedback, а не через собственную копию логики.
# ---------------------------------------------------------------------------

def test_on_show_routes_through_attach_copy_feedback(monkeypatch):
    """RED до рефакторинга: _on_show использовал свою копию логики
    (locals copy_action) и не звал os_patch.attach_copy_feedback вовсе."""
    notifier = _make_notifier(monkeypatch)

    calls = []

    def fake_attach(button, owner, get_text, **kwargs):
        calls.append((button, owner, get_text, kwargs))
        return lambda: None

    monkeypatch.setattr(os_patch, "attach_copy_feedback", fake_attach, raising=False)

    notifier._on_show("Заголовок", "Текст блокировки")

    assert len(calls) == 1, (
        "DeadlockNotifier._on_show должен настраивать кнопку копирования "
        "через os_patch.attach_copy_feedback, а не через собственную копию логики"
    )
    button, owner, get_text, kwargs = calls[0]
    assert isinstance(button, QtWidgets.QAbstractButton)
    assert get_text() == "Текст блокировки"


# ---------------------------------------------------------------------------
# (а) Характеризационный тест итогового поведения канонической реализации,
# как оно проявляется через DeadlockNotifier: копирование + сброс через
# duration_ms (которого раньше не было вовсе).
# ---------------------------------------------------------------------------

def test_on_show_copy_button_copies_text_and_resets_after_duration(monkeypatch):
    captured = {}
    original_attach = os_patch.attach_copy_feedback

    def spying_attach(button, owner, get_text, **kwargs):
        # Форсируем короткий duration_ms, чтобы не ждать реальные 2с —
        # _on_show вызывает attach_copy_feedback без явного duration_ms
        # (используется дефолт), поэтому override здесь безопасен: он не
        # меняет поведение продакшен-кода, только ускоряет тест.
        kwargs["duration_ms"] = 30
        captured["button"] = button
        captured["owner"] = owner
        return original_attach(button, owner, get_text, **kwargs)

    monkeypatch.setattr(os_patch, "attach_copy_feedback", spying_attach)
    notifier = _make_notifier(monkeypatch)

    notifier._on_show("Заголовок", "=== ИНФОРМАЦИЯ ДЛЯ ОТЛАДКИ\nстек вызовов")

    button = captured["button"]
    original_label = button.text()

    button.click()

    assert QtWidgets.QApplication.clipboard().text() == (
        "=== ИНФОРМАЦИЯ ДЛЯ ОТЛАДКИ\nстек вызовов"
    )
    assert button.text() == "Скопировано!"
    assert button.isEnabled() is False

    QTest.qWait(200)

    assert button.text() == original_label
    assert button.isEnabled() is True
