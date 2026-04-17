# -*- coding: utf-8 -*-

from PyQt6.QtWidgets import QApplication, QMessageBox, QWidget


EXIT_CODE_REBOOT = 2000


def prompt_return_to_menu(parent: QWidget, title="Завершение работы") -> str:
    msg_box = QMessageBox(parent)
    msg_box.setWindowTitle(title)
    msg_box.setText("Закрыть программу или вернуться в главное меню?")
    msg_box.setIcon(QMessageBox.Icon.Question)

    btn_menu = msg_box.addButton("Вернуться в меню", QMessageBox.ButtonRole.ActionRole)
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
