# -*- coding: utf-8 -*-
import json
from PyQt6 import QtWidgets, QtCore
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QComboBox,
    QCheckBox, QPushButton, QDialogButtonBox, QTableWidget, QHeaderView,
    QTableWidgetItem, QMessageBox
)
from PyQt6.QtCore import Qt
from ...utils.settings import SettingsManager

class ProxySettingsDialog(QDialog):
    def __init__(self, parent=None, settings_manager=None):
        super().__init__(parent)
        self.setWindowTitle("Настройки прокси")
        self.setMinimumWidth(500)
        if settings_manager is None:
            app = QtWidgets.QApplication.instance()
            if not hasattr(app, 'settings_manager'):
                raise RuntimeError("SettingsManager не найден.")
            self.settings_manager = app.get_settings_manager()
        else:
            self.settings_manager = settings_manager
        self.init_ui()
        self.load_settings()

    def init_ui(self):
        main_layout = QVBoxLayout(self)

        # --- Верхняя половина: таблица сохраненных прокси ---
        self.saved_proxies_label = QLabel("Сохраненные прокси:")
        main_layout.addWidget(self.saved_proxies_label)

        self.proxies_table = QTableWidget()
        self.proxies_table.setColumnCount(4)  # Тип, Хост, Порт, Пользователь
        self.proxies_table.setHorizontalHeaderLabels(["Тип", "Хост", "Порт", "Пользователь"])
        header = self.proxies_table.horizontalHeader()
        header.setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)  # Автоматическое изменение размеров
        self.proxies_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.proxies_table.itemSelectionChanged.connect(self.populate_edit_fields)  # Связываем с функцией заполнения полей
        main_layout.addWidget(self.proxies_table)

        # --- Нижняя половина: редактирование текущего прокси ---
        self.current_proxy_group = QtWidgets.QGroupBox("Настройки текущего прокси")
        current_proxy_layout = QVBoxLayout(self.current_proxy_group)

        # Тип прокси
        proxy_type_layout = QHBoxLayout()
        self.proxy_type_label = QLabel("Тип:")
        self.proxy_type_combo = QComboBox()
        self.proxy_type_combo.addItems(["SOCKS5", "SOCKS4", "HTTP"])
        proxy_type_layout.addWidget(self.proxy_type_label)
        proxy_type_layout.addWidget(self.proxy_type_combo)
        current_proxy_layout.addLayout(proxy_type_layout)

        # Хост
        self.proxy_host_label = QLabel("Хост:")
        self.proxy_host_edit = QLineEdit()
        current_proxy_layout.addWidget(self.proxy_host_label)
        current_proxy_layout.addWidget(self.proxy_host_edit)

        # Порт
        self.proxy_port_label = QLabel("Порт:")
        self.proxy_port_edit = QLineEdit()
        current_proxy_layout.addWidget(self.proxy_port_label)
        current_proxy_layout.addWidget(self.proxy_port_edit)

        # Пользователь
        self.proxy_user_label = QLabel("Пользователь:")
        self.proxy_user_edit = QLineEdit()
        current_proxy_layout.addWidget(self.proxy_user_label)
        current_proxy_layout.addWidget(self.proxy_user_edit)

        # Пароль (добавим, если нужно)
        self.proxy_pass_label = QLabel("Пароль:")
        self.proxy_pass_edit = QLineEdit()
        self.proxy_pass_edit.setEchoMode(QLineEdit.EchoMode.Password)  # Скрываем пароль
        current_proxy_layout.addWidget(self.proxy_pass_label)
        current_proxy_layout.addWidget(self.proxy_pass_edit)

        # Включить прокси
        self.proxy_enabled_checkbox = QCheckBox("Включить прокси")
        current_proxy_layout.addWidget(self.proxy_enabled_checkbox)

        main_layout.addWidget(self.current_proxy_group)

        # Кнопки
        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        ok_button = button_box.button(QDialogButtonBox.StandardButton.Ok)
        ok_button.setText("Принять")  # Изменяем текст кнопки "Ok"
        cancel_button = button_box.button(QDialogButtonBox.StandardButton.Cancel)
        cancel_button.setText("Отмена")  # Изменяем текст кнопки "Cancel"
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        main_layout.addWidget(button_box)

        # Добавляем кнопку удаления
        self.delete_button = QPushButton("Удалить прокси")
        self.delete_button.clicked.connect(self.delete_selected_proxy)
        button_box.addButton(self.delete_button, QDialogButtonBox.ButtonRole.ActionRole) # Добавляем кнопку удаления

    def populate_edit_fields(self):
        """Заполняет поля редактирования выбранными данными из таблицы."""
        selected_items = self.proxies_table.selectedItems()
        if not selected_items:
            self.clear_edit_fields()
            return

        # Получаем первую строку (выбрана только одна строка)
        row = selected_items[0].row()
        self.proxy_type_combo.setCurrentText(self.proxies_table.item(row, 0).text())
        self.proxy_host_edit.setText(self.proxies_table.item(row, 1).text())
        self.proxy_port_edit.setText(self.proxies_table.item(row, 2).text())
        self.proxy_user_edit.setText(self.proxies_table.item(row, 3).text())

        # Загружаем пароль из настроек (если он есть)
        settings = self.settings_manager.load_proxy_settings()
        selected_proxy = self.get_proxy_from_table_row(row)
        if selected_proxy:
            self.proxy_pass_edit.setText(selected_proxy.get('pass', ''))

    def clear_edit_fields(self):
        """Очищает поля редактирования."""
        self.proxy_type_combo.setCurrentIndex(0)  # Сбрасываем тип прокси
        self.proxy_host_edit.clear()
        self.proxy_port_edit.clear()
        self.proxy_user_edit.clear()
        self.proxy_pass_edit.clear()

    def get_proxy_from_table_row(self, row):
        """Извлекает данные прокси из строки таблицы."""
        try:
            proxy_type = self.proxies_table.item(row, 0).text()
            proxy_host = self.proxies_table.item(row, 1).text()
            proxy_port = self.proxies_table.item(row, 2).text()
            proxy_user = self.proxies_table.item(row, 3).text()

            # Load settings to get password
            settings = self.settings_manager.load_proxy_settings()
            saved_proxies = settings.get("saved_proxies", [])

            # Find the proxy by matching all fields, as a simple index match may not be accurate
            for proxy in saved_proxies:
                if (proxy.get("type") == proxy_type and
                    proxy.get("host") == proxy_host and
                    str(proxy.get("port")) == proxy_port and  # Ensure port matches as string
                    proxy.get("user") == proxy_user):
                    return proxy

        except Exception as e:
            print(f"[ERROR] Could not get proxy from row: {e}")
        return None

    def load_settings(self):
        """Загружает настройки прокси и заполняет таблицу."""
        settings = self.settings_manager.load_proxy_settings()
        saved_proxies = settings.get("saved_proxies", [])

        # Clear existing rows
        self.proxies_table.setRowCount(0)

        # Fill the table with data
        for row_index, proxy in enumerate(saved_proxies):
            self.proxies_table.insertRow(row_index)

            # Set the values for each column
            self.proxies_table.setItem(row_index, 0, QTableWidgetItem(proxy.get("type", "SOCKS5")))
            self.proxies_table.setItem(row_index, 1, QTableWidgetItem(proxy.get("host", "")))
            self.proxies_table.setItem(row_index, 2, QTableWidgetItem(str(proxy.get("port", ""))))
            self.proxies_table.setItem(row_index, 3, QTableWidgetItem(proxy.get("user", "")))

        # Load current proxy settings
        self.proxy_type_combo.setCurrentText(settings.get('type', 'SOCKS5'))
        self.proxy_host_edit.setText(settings.get('host', ''))
        self.proxy_port_edit.setText(str(settings.get('port', '')))
        self.proxy_user_edit.setText(settings.get('user', ''))
        self.proxy_pass_edit.setText(settings.get('pass', ''))
        self.proxy_enabled_checkbox.setChecked(settings.get('enabled', False))

    def accept(self):
        """Сохраняет настройки прокси."""
        if not self.validate_inputs():
            return

        proxy_settings = {
            'enabled': self.proxy_enabled_checkbox.isChecked(),
            'type': self.proxy_type_combo.currentText(),
            'host': self.proxy_host_edit.text(),
            'port': int(self.proxy_port_edit.text()),
            'user': self.proxy_user_edit.text(),
            'pass': self.proxy_pass_edit.text()
        }

        saved_proxies = self.settings_manager.load_proxy_settings().get("saved_proxies", [])
        is_duplicate = False

        for proxy in saved_proxies:
            if (proxy.get("type") == proxy_settings.get("type") and
                proxy.get("host") == proxy_settings.get("host") and
                str(proxy.get("port")) == str(proxy_settings.get("port")) and
                proxy.get("user") == proxy_settings.get("user")):
                is_duplicate = True
                break

        if not is_duplicate:
            saved_proxies.append(proxy_settings)

        self.settings_manager.save_proxy_settings(
            {
                "enabled": self.proxy_enabled_checkbox.isChecked(),
                "type": self.proxy_type_combo.currentText(),
                "host": self.proxy_host_edit.text(),
                "port": int(self.proxy_port_edit.text()),
                "user": self.proxy_user_edit.text(),
                "pass": self.proxy_pass_edit.text(),
                "saved_proxies": saved_proxies
            }
        )

        super().accept()

    def validate_inputs(self):
        """Проверяет введенные данные."""
        try:
            port = int(self.proxy_port_edit.text())
            if not (1 <= port <= 65535):
                QMessageBox.warning(self, "Warning", "Порт должен быть числом от 1 до 65535.")
                return False
        except ValueError:
            QMessageBox.warning(self, "Warning", "Неверный формат порта.")
            return False
        if not self.proxy_host_edit.text():
            QMessageBox.warning(self, "Warning", "Укажите хост прокси-сервера.")
            return False
        return True

    def delete_selected_proxy(self):
        """Удаляет выбранный прокси из списка."""
        selected_items = self.proxies_table.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "Предупреждение", "Выберите прокси для удаления.")
            return
    
        row = selected_items[0].row()
        proxy_to_delete = self.get_proxy_from_table_row(row)
    
        if proxy_to_delete:
            saved_proxies = self.settings_manager.load_proxy_settings().get("saved_proxies", [])
            try:
                saved_proxies.remove(proxy_to_delete)
            except ValueError:
                QMessageBox.warning(self, "Ошибка", "Не удалось удалить прокси.")
                return
            self.settings_manager.save_proxy_settings({"saved_proxies": saved_proxies, "enabled": self.proxy_enabled_checkbox.isChecked(),
                "type": self.proxy_type_combo.currentText(),
                "host": self.proxy_host_edit.text(),
                "port": int(self.proxy_port_edit.text()),
                "user": self.proxy_user_edit.text(),
                "pass": self.proxy_pass_edit.text()})
            self.load_settings()  # Обновляем таблицу
        else:
            QMessageBox.warning(self, "Ошибка", "Не удалось найти выбранный прокси.")
