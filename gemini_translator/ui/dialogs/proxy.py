# -*- coding: utf-8 -*-
import os
from PyQt6 import QtWidgets
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QComboBox,
    QCheckBox, QPushButton, QDialogButtonBox, QTableWidget,
    QTableWidgetItem, QMessageBox
)

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
        bus = getattr(self.settings_manager, "bus", None)
        if bus is not None and hasattr(bus, "event_posted"):
            bus.event_posted.connect(self._on_proxy_event)

    def init_ui(self):
        main_layout = QVBoxLayout(self)

        # --- Верхняя половина: таблица сохраненных прокси ---
        self.saved_proxies_label = QLabel("Сохраненные прокси:")
        main_layout.addWidget(self.saved_proxies_label)

        self.proxies_table = QTableWidget()
        self.proxies_table.setAlternatingRowColors(True)
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

        # Режим подключения
        tunnel_mode_layout = QHBoxLayout()
        self.tunnel_mode_label = QLabel("Режим:")
        self.tunnel_mode_combo = QComboBox()
        self.tunnel_mode_combo.addItems(["Обычный прокси", "Автотуннель через SSH"])
        self.tunnel_mode_combo.currentTextChanged.connect(self._on_tunnel_mode_changed)
        tunnel_mode_layout.addWidget(self.tunnel_mode_label)
        tunnel_mode_layout.addWidget(self.tunnel_mode_combo)
        current_proxy_layout.addLayout(tunnel_mode_layout)

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

        # Параметры удалённого SSH-сервера. Приватный ключ не читается:
        # приложение сохраняет только путь и передаёт его системному ssh.
        self.ssh_host_label = QLabel("SSH-хост:")
        self.ssh_host_edit = QLineEdit()
        current_proxy_layout.addWidget(self.ssh_host_label)
        current_proxy_layout.addWidget(self.ssh_host_edit)

        self.ssh_port_label = QLabel("SSH-порт:")
        self.ssh_port_edit = QLineEdit("22")
        current_proxy_layout.addWidget(self.ssh_port_label)
        current_proxy_layout.addWidget(self.ssh_port_edit)

        self.ssh_user_label = QLabel("SSH-пользователь:")
        self.ssh_user_edit = QLineEdit()
        current_proxy_layout.addWidget(self.ssh_user_label)
        current_proxy_layout.addWidget(self.ssh_user_edit)

        self.ssh_key_path_label = QLabel("Приватный ключ SSH:")
        current_proxy_layout.addWidget(self.ssh_key_path_label)
        ssh_key_layout = QHBoxLayout()
        self.ssh_key_path_edit = QLineEdit()
        self.ssh_key_browse_btn = QPushButton("Обзор…")
        self.ssh_key_browse_btn.clicked.connect(self._browse_ssh_key)
        ssh_key_layout.addWidget(self.ssh_key_path_edit)
        ssh_key_layout.addWidget(self.ssh_key_browse_btn)
        current_proxy_layout.addLayout(ssh_key_layout)

        self.tunnel_status_label = QLabel("Статус туннеля: отключён")
        current_proxy_layout.addWidget(self.tunnel_status_label)

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
            is_ssh = selected_proxy.get("tunnel_mode") == "ssh"
            self.tunnel_mode_combo.setCurrentText(
                "Автотуннель через SSH" if is_ssh else "Обычный прокси"
            )
            self.ssh_host_edit.setText(selected_proxy.get("ssh_host", ""))
            self.ssh_port_edit.setText(str(selected_proxy.get("ssh_port", 22)))
            self.ssh_user_edit.setText(selected_proxy.get("ssh_user", ""))
            self.ssh_key_path_edit.setText(selected_proxy.get("ssh_key_path", ""))
            self._on_tunnel_mode_changed(self.tunnel_mode_combo.currentText())

    def clear_edit_fields(self):
        """Очищает поля редактирования."""
        self.proxy_type_combo.setCurrentIndex(0)  # Сбрасываем тип прокси
        self.proxy_host_edit.clear()
        self.proxy_port_edit.clear()
        self.proxy_user_edit.clear()
        self.proxy_pass_edit.clear()
        self.tunnel_mode_combo.setCurrentText("Обычный прокси")
        self.ssh_host_edit.clear()
        self.ssh_port_edit.setText("22")
        self.ssh_user_edit.clear()
        self.ssh_key_path_edit.clear()
        self._on_tunnel_mode_changed(self.tunnel_mode_combo.currentText())

    def _is_ssh_mode(self):
        return self.tunnel_mode_combo.currentText() == "Автотуннель через SSH"

    def _on_tunnel_mode_changed(self, _text):
        is_ssh = self._is_ssh_mode()
        for widget in (
            self.ssh_host_label, self.ssh_host_edit,
            self.ssh_port_label, self.ssh_port_edit,
            self.ssh_user_label, self.ssh_user_edit,
            self.ssh_key_path_label, self.ssh_key_path_edit,
            self.ssh_key_browse_btn, self.tunnel_status_label,
        ):
            widget.setVisible(is_ssh)

        self.proxy_user_label.setVisible(not is_ssh)
        self.proxy_user_edit.setVisible(not is_ssh)
        self.proxy_pass_label.setVisible(not is_ssh)
        self.proxy_pass_edit.setVisible(not is_ssh)
        self.proxy_type_combo.setEnabled(not is_ssh)

        if is_ssh:
            # `ssh -D` создаёт SOCKS5 на локальном интерфейсе.
            self.proxy_type_combo.setCurrentText("SOCKS5")
            self.proxy_host_edit.setText("127.0.0.1")
            self.proxy_host_edit.setEnabled(False)
            self.proxy_host_label.setText("Локальный хост:")
            self.proxy_port_label.setText("Локальный порт туннеля:")
        else:
            self.proxy_host_edit.setEnabled(True)
            self.proxy_host_label.setText("Хост:")
            self.proxy_port_label.setText("Порт:")

    def _browse_ssh_key(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Выберите приватный ключ SSH"
        )
        if path:
            self.ssh_key_path_edit.setText(path)

    def _on_proxy_event(self, event):
        if event.get("event") != "current_proxy_status":
            return
        data = event.get("data", {})
        state = data.get("tunnel_state")
        if state is None:
            return
        message = str(data.get("tunnel_message") or "")
        labels = {
            "connecting": "Статус туннеля: подключение…",
            "up": "Статус туннеля: активен",
            "down": "Статус туннеля: отключён",
            "error": "Статус туннеля: ошибка",
        }
        text = labels.get(state, f"Статус туннеля: {state}")
        if message and state in {"down", "error"}:
            text += f" ({message})"
        self.tunnel_status_label.setText(text)

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
        is_ssh = settings.get("tunnel_mode") == "ssh"
        self.tunnel_mode_combo.setCurrentText(
            "Автотуннель через SSH" if is_ssh else "Обычный прокси"
        )
        self.ssh_host_edit.setText(settings.get("ssh_host", ""))
        self.ssh_port_edit.setText(str(settings.get("ssh_port", 22)))
        self.ssh_user_edit.setText(settings.get("ssh_user", ""))
        self.ssh_key_path_edit.setText(settings.get("ssh_key_path", ""))
        self._on_tunnel_mode_changed(self.tunnel_mode_combo.currentText())
        if is_ssh and settings.get("enabled"):
            self.tunnel_status_label.setText("Статус туннеля: ожидание подключения…")

    def accept(self):
        """Сохраняет настройки прокси."""
        if not self.validate_inputs():
            return

        proxy_settings = self._collect_proxy_settings()

        saved_proxies = self.settings_manager.load_proxy_settings().get("saved_proxies", [])
        for index, proxy in enumerate(saved_proxies):
            if self._proxy_identity(proxy) == self._proxy_identity(proxy_settings):
                saved_proxies[index] = dict(proxy_settings)
                break
        else:
            saved_proxies.append(proxy_settings)

        settings_to_save = dict(proxy_settings)
        settings_to_save["saved_proxies"] = saved_proxies
        self.settings_manager.save_proxy_settings(settings_to_save)

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

        if self._is_ssh_mode():
            if not self.ssh_host_edit.text().strip():
                QMessageBox.warning(self, "Warning", "Укажите SSH-хост.")
                return False
            try:
                ssh_port = int(self.ssh_port_edit.text())
                if not 1 <= ssh_port <= 65535:
                    raise ValueError
            except ValueError:
                QMessageBox.warning(self, "Warning", "SSH-порт должен быть числом от 1 до 65535.")
                return False
            if not self.ssh_user_edit.text().strip():
                QMessageBox.warning(self, "Warning", "Укажите SSH-пользователя.")
                return False
            key_path = os.path.expanduser(self.ssh_key_path_edit.text().strip())
            if not key_path or not os.path.isfile(key_path):
                QMessageBox.warning(self, "Warning", "Укажите существующий файл приватного ключа.")
                return False
        return True

    def _collect_proxy_settings(self):
        is_ssh = self._is_ssh_mode()
        return {
            "enabled": self.proxy_enabled_checkbox.isChecked(),
            "type": "SOCKS5" if is_ssh else self.proxy_type_combo.currentText(),
            "host": "127.0.0.1" if is_ssh else self.proxy_host_edit.text().strip(),
            "port": int(self.proxy_port_edit.text()),
            "user": "" if is_ssh else self.proxy_user_edit.text(),
            "pass": "" if is_ssh else self.proxy_pass_edit.text(),
            "tunnel_mode": "ssh" if is_ssh else "none",
            "ssh_host": self.ssh_host_edit.text().strip(),
            "ssh_port": int(self.ssh_port_edit.text() or 22),
            "ssh_user": self.ssh_user_edit.text().strip(),
            "ssh_key_path": os.path.expanduser(self.ssh_key_path_edit.text().strip()),
        }

    @staticmethod
    def _proxy_identity(proxy):
        if proxy.get("tunnel_mode") == "ssh":
            return (
                "ssh",
                str(proxy.get("ssh_host") or ""),
                str(proxy.get("ssh_port") or 22),
                str(proxy.get("ssh_user") or ""),
                str(proxy.get("port") or ""),
            )
        return (
            "none",
            str(proxy.get("type") or "SOCKS5"),
            str(proxy.get("host") or ""),
            str(proxy.get("port") or ""),
            str(proxy.get("user") or ""),
        )

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
            current_settings = self._collect_proxy_settings()
            current_settings["saved_proxies"] = saved_proxies
            self.settings_manager.save_proxy_settings(current_settings)
            self.load_settings()  # Обновляем таблицу
        else:
            QMessageBox.warning(self, "Ошибка", "Не удалось найти выбранный прокси.")
