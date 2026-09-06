# -*- coding: utf-8 -*-

import os

from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox, QWidget


EXIT_CODE_REBOOT = 2000


class PageDialogProxyMixin:
    """Миксин для тонких QDialog-обёрток, хостящих виджет ``.page`` и
    сохраняющих старый exec()-API диалога поверх новой ``ShellPage``.

    Делегирует обращения к НЕИЗВЕСТНЫМ атрибутам экземпляра в ``self.page``
    (было продублировано как минимум в 14 обёртках — см. cluster-17).
    """

    def __getattr__(self, name):
        page = self.__dict__.get("page")
        if page is not None:
            return getattr(page, name)
        raise AttributeError(name)


def _resolve_page_class_attr(page_cls, name):
    """Точка, через которую метаклассы тонких диалогов достают атрибуты
    КЛАССА Page. Вынесена отдельно от ``make_page_delegating_meta``, чтобы
    её можно было подменить в тестах-маршрутизации."""
    return getattr(page_cls, name)


def make_page_delegating_meta(page_cls):
    """Строит метакласс для тонкой QDialog-обёртки над ``page_cls``.

    Делегирует обращения к неизвестным атрибутам КЛАССА (например,
    ``TranslationValidatorDialog._some_method``, которым пользуются тесты,
    забирающие unbound-методы старых диалогов) в ``page_cls`` — раньше это
    была своя ``_XxxDialogMeta`` на каждую обёртку.
    """

    class _PageDelegatingMeta(type(QDialog)):
        def __getattr__(cls, name):
            return _resolve_page_class_attr(page_cls, name)

    _PageDelegatingMeta.__name__ = f"_{page_cls.__name__}DelegatingMeta"
    _PageDelegatingMeta.__qualname__ = _PageDelegatingMeta.__name__
    _PageDelegatingMeta._page_cls = page_cls
    return _PageDelegatingMeta


def _translator_only_mode_enabled() -> bool:
    return os.environ.get("GT_TRANSLATOR_ONLY_MODE", "").strip() == "1"


def prompt_return_to_menu(parent: QWidget, title="Завершение работы") -> str:
    msg_box = QMessageBox(parent)
    msg_box.setWindowTitle(title)

    if _translator_only_mode_enabled():
        msg_box.setText("Закрыть программу или открыть переводчик заново?")
        menu_button_text = "Новый проект"
    else:
        msg_box.setText("Закрыть программу или вернуться в главное меню?")
        menu_button_text = "Вернуться в меню"

    msg_box.setIcon(QMessageBox.Icon.Question)

    btn_menu = msg_box.addButton(menu_button_text, QMessageBox.ButtonRole.ActionRole)
    btn_exit = msg_box.addButton("Выйти из программы", QMessageBox.ButtonRole.DestructiveRole)
    btn_cancel = msg_box.addButton("Отмена", QMessageBox.ButtonRole.RejectRole)

    msg_box.exec()
    clicked = msg_box.clickedButton()

    if clicked == btn_menu:
        return "menu"
    if clicked == btn_exit:
        return "exit"
    return "cancel"


def return_to_main_menu():
    QApplication.exit(EXIT_CODE_REBOOT)


def post_session_separator(post_event, session_id_log: str, reason: str):
    final_message_data = {
        'message': f"■■■ СЕССИЯ {session_id_log[:8]} ОСТАНОВЛЕНА. {reason} ■■■",
        'priority': 'final',
    }
    post_event('log_message', {'message': "---SEPARATOR---"})
    post_event('log_message', final_message_data)
