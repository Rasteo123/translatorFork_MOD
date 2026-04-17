from .common_widgets import NoScrollSpinBox, NoScrollDoubleSpinBox, NoScrollComboBox
from ...api import config as api_config
from ..dialogs.misc import KeyInputDialog, DeleteKeysDialog, CustomListWidget
from ...utils.settings import SettingsManager
from PyQt6.QtCore import pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QPushButton, QGroupBox, QHBoxLayout,
    QMessageBox, QAbstractItemView, QSplitter, QLabel, QDialog, QComboBox, QDialogButtonBox
)
from PyQt6 import QtWidgets, QtCore, QtGui
import time
import threading


class AdaptiveControlsWidget(QWidget):
    """
    Адаптивный виджет-контейнер, который динамически изменяет стиль
    дочерних кнопок в зависимости от доступной ему вертикальной высоты.
    """

    def __init__(self, arrow_buttons, action_buttons, separator, parent=None):
        super().__init__(parent)
        self.arrow_buttons = arrow_buttons
        self.action_buttons = action_buttons
        self.all_buttons = arrow_buttons + action_buttons

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)

        layout.addStretch(10)
        for btn in self.arrow_buttons:
            layout.addWidget(btn)
            layout.addStretch(1)

        if layout.itemAt(layout.count() - 1).spacerItem():
            layout.takeAt(layout.count() - 1)

        layout.addStretch(5)
        layout.addWidget(separator)
        layout.addStretch(5)

        for btn in self.action_buttons:
            layout.addWidget(btn)
            layout.addStretch(1)

        if layout.itemAt(layout.count() - 1).spacerItem():
            layout.takeAt(layout.count() - 1)

        layout.addStretch(10)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        height = self.height()
        font_size = max(8, min(12, int(height / 35)))
        vertical_padding = max(1, int(font_size / 4))
        horizontal_padding = vertical_padding + 3

        base_style = f"""
            QPushButton {{
                padding-top: {vertical_padding}px;
                padding-bottom: {vertical_padding}px;
                padding-left: {horizontal_padding}px;
                padding-right: {horizontal_padding}px;
                font-size: {font_size}pt;
            }}
        """
        arrow_style_addon = "font-weight: bold;"

        for btn in self.all_buttons:
            btn.setStyleSheet(base_style)

        for btn in self.arrow_buttons:
            btn.setStyleSheet(btn.styleSheet() + arrow_style_addon)


class ProviderChoiceDialog(QDialog):
    def __init__(self, items, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Выбор сервиса")
        layout = QVBoxLayout(self)
        label = QLabel("К какому сервису относятся эти ключи?")
        layout.addWidget(label)
        self.combo_box = QComboBox(self)
        self.combo_box.addItems(items)
        layout.addWidget(self.combo_box)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Да")
        buttons.button(
            QDialogButtonBox.StandardButton.Cancel).setText("Отменить")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_selected_item(self):
        return self.combo_box.currentText()


class KeyManagementWidget(QWidget):
    active_keys_changed = pyqtSignal()

    def __init__(self, settings_manager: SettingsManager, parent=None, distribution_group_widget=None, current_active_keys=None, server_manager=None):
        super().__init__(parent)
        self.settings_manager = settings_manager
        self.distribution_group_widget = distribution_group_widget
        self.server_manager = server_manager

        self.current_active_keys_by_provider = {}
        self.current_model_id = None
        if current_active_keys:
            provider_id = self.get_selected_provider() if hasattr(
                self, 'provider_combo') else 'gemini'
            self.current_active_keys_by_provider[provider_id] = set(
                current_active_keys)

        app = QtWidgets.QApplication.instance()
        if app and hasattr(app, 'event_bus'):
            self.bus = app.event_bus
            self.bus.event_posted.connect(self.on_event)
        else:
            class DummyBus:
                def emit(self, *args, **kwargs): pass
            self.bus = DummyBus()

        self.init_ui()
        self._load_and_refresh_keys()

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        key_splitter = QSplitter(QtCore.Qt.Orientation.Horizontal)

        # Левая панель
        left_panel_widget = QWidget()
        left_panel_layout = QVBoxLayout(left_panel_widget)

        provider_group = QGroupBox("1. Выбор сервиса")
        provider_layout = QHBoxLayout(provider_group)

        self.provider_combo = NoScrollComboBox()
        self.provider_combo.clear()
        for p_id, p_data in api_config.api_providers().items():
            if p_data.get('visible', True):
                self.provider_combo.addItem(
                    p_data['display_name'], userData=p_id)

        self.key_count_label = QLabel("Ключи: 0 (✅ 0 / ❌ 0)")
        self.key_count_label.setStyleSheet("font-size: 10pt; color: #aaa;")

        self.server_button = QPushButton("Запустить сервер")
        self.server_button.setStyleSheet("""
            QPushButton { background-color: #2e7d32; color: white; font-weight: bold; padding: 10px; border-radius: 4px; }
            QPushButton:hover { background-color: #388e3c; }
            QPushButton:disabled { background-color: #1b5e20; color: #888; }
        """)
        self.server_button.clicked.connect(self._toggle_server)

        provider_layout.addWidget(self.provider_combo)
        provider_layout.addWidget(self.server_button)
        provider_layout.addStretch(1)
        provider_layout.addWidget(self.key_count_label)

        left_panel_layout.addWidget(provider_group)

        self.available_keys_group = QGroupBox("2. Доступные ключи (общий пул)")
        available_layout = QVBoxLayout(self.available_keys_group)
        self.available_keys_list = CustomListWidget()
        self.available_keys_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self.available_keys_list.doubleClicked.connect(
            self._add_selected_to_active)
        self.available_keys_list.setToolTip(
            "Нажмите Delete, чтобы удалить ключ из пула.\nДвойной клик для перемещения в активные.")
        self.available_keys_list.delete_pressed.connect(
            self._handle_delete_from_pool)
        available_layout.addWidget(self.available_keys_list)
        left_panel_layout.addWidget(self.available_keys_group, 1)

        key_splitter.addWidget(left_panel_widget)

        # Центральная панель с кнопками
        self.add_selected_btn = QPushButton(" > ")
        self.add_selected_btn.setToolTip(
            "Переместить выделенные ключи в активные")
        self.add_selected_btn.clicked.connect(self._add_selected_to_active)

        self.remove_selected_btn = QPushButton(" < ")
        self.remove_selected_btn.setToolTip("Вернуть выделенные ключи в пул")
        self.remove_selected_btn.clicked.connect(
            self._remove_selected_from_active)

        self.add_all_btn = QPushButton(" >> ")
        self.add_all_btn.setToolTip("Добавить все рабочие ('зеленые') ключи")
        self.add_all_btn.clicked.connect(self._add_all_to_active)

        self.remove_all_btn = QPushButton(" << ")
        self.remove_all_btn.setToolTip("Вернуть все ключи в пул")
        self.remove_all_btn.clicked.connect(self._remove_all_from_active)

        separator = QtWidgets.QFrame()
        separator.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        separator.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)

        self.add_from_text_btn = QPushButton("Добавить…")
        self.add_from_text_btn.setToolTip(
            "Добавить новые ключи из текста или файла")
        self.add_from_text_btn.clicked.connect(self._add_keys_from_text)

        self.reset_selected_btn = QPushButton("'Отбелить'…")
        self.reset_selected_btn.setToolTip(
            "Сбросить статус ('отбелить') для выделенных ключей")
        self.reset_selected_btn.clicked.connect(self._reset_selected_statuses)

        self.force_exhaust_btn = QPushButton("'Покраснить'…")
        self.force_exhaust_btn.setToolTip(
            "Принудительно пометить выделенные ключи как исчерпанные")
        self.force_exhaust_btn.clicked.connect(self._force_exhaust_selected)

        self.remove_from_pool_btn = QPushButton("Удалить…")
        self.remove_from_pool_btn.setToolTip(
            "Удалить выделенные ключи из пула навсегда")
        self.remove_from_pool_btn.clicked.connect(
            self._handle_delete_from_pool)

        arrow_buttons = [self.add_selected_btn, self.remove_selected_btn,
                         self.add_all_btn, self.remove_all_btn]
        action_buttons = [self.add_from_text_btn,
                          self.reset_selected_btn, self.force_exhaust_btn, self.remove_from_pool_btn]

        controls_widget = AdaptiveControlsWidget(
            arrow_buttons, action_buttons, separator, self)
        key_splitter.addWidget(controls_widget)

        # Правая панель
        right_panel_container = QWidget()
        right_panel_layout = QVBoxLayout(right_panel_container)

        if self.distribution_group_widget:
            self.distribution_group_widget.setObjectName("distribution_group")
            right_panel_layout.addWidget(self.distribution_group_widget)

        self.active_keys_group = QGroupBox("3. Активные ключи для сессии")
        active_layout = QVBoxLayout(self.active_keys_group)

        active_header_widget = QWidget()
        active_header_layout = QHBoxLayout(active_header_widget)
        active_header_layout.setContentsMargins(0, 0, 0, 0)

        self.active_key_count_label = QLabel("Выбрано: 0")
        self.active_key_count_label.setStyleSheet(
            "font-size: 10pt; color: #aaa;")

        active_header_layout.addStretch()
        active_header_layout.addWidget(self.active_key_count_label)
        active_layout.addWidget(active_header_widget)

        self.active_keys_list = CustomListWidget()
        self.active_keys_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self.active_keys_list.doubleClicked.connect(
            self._remove_selected_from_active)
        self.active_keys_list.setToolTip(
            "Нажмите Delete или двойной клик для возврата в пул.")
        self.active_keys_list.delete_pressed.connect(
            self._remove_selected_from_active)

        active_layout.addWidget(self.active_keys_list)
        right_panel_layout.addWidget(self.active_keys_group, 1)

        key_splitter.addWidget(right_panel_container)
        key_splitter.setSizes([1000, 1, 1000])

        main_layout.addWidget(key_splitter)

        self._on_provider_changed(self.provider_combo.currentIndex())
        self.provider_combo.currentIndexChanged.connect(
            self._on_provider_changed)

        self._update_server_button_visibility()
        if self.server_manager:
            self.server_manager.server_status_changed.connect(
                self._update_server_button)


    def _toggle_server(self):
        if not self.server_manager:
            return

        # Получаем ID текущего выбранного провайдера из комбобокса
        provider_id = self.provider_combo.currentData()

        if self.server_manager.is_server_running():
            self.server_manager.stop_server()
        else:
            # ПЕРЕДАЕМ provider_id В МЕТОД ЗАПУСКА
            self.server_manager.start_server(provider_id=provider_id)

            # Сбор и валидация ключей (остается без изменений)
            keys_to_validate = []
            for i in range(self.available_keys_list.count()):
                item = self.available_keys_list.item(i)
                key = item.data(QtCore.Qt.ItemDataRole.UserRole)
                if key:
                    keys_to_validate.append(key)

            if keys_to_validate:
                self.bus.event_posted.emit({
                    'event': 'log_message',
                    'data': {'message': f"[SERVER] Запуск сервера... Запланирована проверка {len(keys_to_validate)} ключей."}
                })
                threading.Thread(target=self._validate_keys_background, args=(
                    keys_to_validate,), daemon=True).start()
            else:
                self.bus.event_posted.emit({
                    'event': 'log_message',
                    'data': {'message': "[SERVER] Сервер запущен. Нет доступных ключей для проверки."}
                })

    def _validate_keys_background(self, keys):
        """Фоновая задача для проверки ключей и обновления их статуса в UI."""
        time.sleep(2)
        if not self.server_manager or not self.server_manager.is_server_running():
            return

        results = self.server_manager.validate_tokens_batch(keys)
        if not results:
            return

        self.bus.event_posted.emit({
            'event': 'log_message',
            'data': {'message': "--- СИНХРОНИЗАЦИЯ СТАТУСОВ ---"}
        })

        model_id = self.current_model_id or "default_perplexity"
        changed_any = False

        for res in results:
            key = res['token']
            token_short = f"...{key[-6:]}" if len(key) > 6 else "Unknown"
            is_valid = res['valid']

            if is_valid:
                was_cleared = self.settings_manager.clear_key_exhaustion_status(
                    key, model_id)
                if was_cleared:
                    changed_any = True
                    self.bus.event_posted.emit({
                        'event': 'log_message',
                        'data': {'message': f"✅ {token_short}: Статус восстановлен (Активен)"}
                    })
            else:
                key_info = self.settings_manager.get_key_info(key)
                already_red = False
                if key_info:
                    already_red = self.settings_manager.is_key_limit_active(
                        key_info, model_id)

                if not already_red:
                    self.settings_manager.mark_key_as_exhausted(key, model_id)
                    changed_any = True
                    self.bus.event_posted.emit({
                        'event': 'log_message',
                        'data': {'message': f"❌ {token_short}: Ключ не авторизован ({res['message']})"}
                    })

        if not changed_any:
            self.bus.event_posted.emit({
                'event': 'log_message',
                'data': {'message': "[SERVER] Все ключи проверены и активны."}
            })

        self.bus.event_posted.emit({
            'event': 'log_message',
            'data': {'message': "------------------------------------------------"}
        })

    @pyqtSlot(str)
    def set_current_model(self, model_id: str):
        self.current_model_id = model_id
        self.update_key_styles_for_model(model_id)

    def _provider_requires_api_key(self, provider_id=None):
        provider_id = provider_id or self.get_selected_provider()
        return api_config.provider_requires_api_key(provider_id)

    def _provider_placeholder_key(self, provider_id=None):
        provider_id = provider_id or self.get_selected_provider()
        return api_config.provider_placeholder_api_key(provider_id)

    def _create_virtual_session_item(self, provider_id: str):
        item = QtWidgets.QListWidgetItem("Встроенная браузерная сессия")
        item.setData(
            QtCore.Qt.ItemDataRole.UserRole,
            self._provider_placeholder_key(provider_id),
        )
        item.setForeground(QtGui.QColor("#90EE90"))
        item.setToolTip(
            "Этот сервис использует сохраненный браузерный профиль и не требует API-ключа."
        )
        item.setFlags(item.flags() & ~QtCore.Qt.ItemFlag.ItemIsSelectable)
        return item

    def _apply_provider_mode(self, provider_id: str, provider_display_name: str):
        requires_api_key = self._provider_requires_api_key(provider_id)
        available_title = "2. Доступные ключи" if requires_api_key else "2. Настройка сессии"
        active_title = "3. Активные ключи для сессии" if requires_api_key else "3. Активная сессия"

        self.available_keys_group.setTitle(f"{available_title} ({provider_display_name})")
        self.active_keys_group.setTitle(active_title)

        self.available_keys_list.setEnabled(requires_api_key)
        self.add_selected_btn.setEnabled(requires_api_key)
        self.remove_selected_btn.setEnabled(requires_api_key)
        self.add_all_btn.setEnabled(requires_api_key)
        self.remove_all_btn.setEnabled(requires_api_key)
        self.add_from_text_btn.setEnabled(requires_api_key)
        self.reset_selected_btn.setEnabled(requires_api_key)
        self.force_exhaust_btn.setEnabled(requires_api_key)
        self.remove_from_pool_btn.setEnabled(requires_api_key)

    def get_active_keys(self):
        active_keys = [
            self.active_keys_list.item(i).data(QtCore.Qt.ItemDataRole.UserRole)
            for i in range(self.active_keys_list.count())
        ]
        if active_keys:
            return active_keys
        if not self._provider_requires_api_key():
            placeholder = self._provider_placeholder_key()
            return [placeholder] if placeholder else []
        return []

    def set_active_keys_for_provider(self, provider_id: str, active_keys: list):
        if not provider_id:
            return
        if not self._provider_requires_api_key(provider_id):
            placeholder = self._provider_placeholder_key(provider_id)
            active_keys = [placeholder] if placeholder else []
        self.current_active_keys_by_provider[provider_id] = set(active_keys)
        index_to_set = -1
        for i in range(self.provider_combo.count()):
            if self.provider_combo.itemData(i) == provider_id:
                index_to_set = i
                break

        if index_to_set != -1:
            self.provider_combo.blockSignals(True)
            self.provider_combo.setCurrentIndex(index_to_set)
            self.provider_combo.blockSignals(False)
            self._on_provider_changed(index_to_set)

    def _update_active_key_count(self):
        if not self._provider_requires_api_key():
            self.active_key_count_label.setText("Выбрано: встроенная сессия")
            return
        count = self.active_keys_list.count()
        self.active_key_count_label.setText(f"Выбрано: {count}")

    def _update_key_counts(self):
        if not self._provider_requires_api_key():
            self.key_count_label.setText("API-ключи не требуются")
            return
        green_count, red_count = 0, 0
        provider_id = self.get_selected_provider()
        current_statuses = self.settings_manager.load_key_statuses()

        for key_info in current_statuses:
            if key_info.get('provider') == provider_id:
                if not self.settings_manager.is_key_limit_active(key_info, self.current_model_id):
                    green_count += 1
                else:
                    red_count += 1

        total_keys = green_count + red_count
        self.key_count_label.setText(
            f"Ключи: {total_keys} (✅{green_count}/❌{red_count})")

    @pyqtSlot(dict)
    def on_event(self, event: dict):
        event_name = event.get('event')
        data = event.get('data', {})

        if event.get('source') == 'KeyManagementWidget' and event.get('event') == 'key_statuses_updated':
            self._load_and_refresh_keys()
            return

        if event.get('event') == 'model_changed':
            model_id = event.get('data', {}).get('model_id')
            if model_id:
                self.set_current_model(model_id)

        if event_name == 'session_started':
            settings_data = data.get('settings', {})
            model_id_for_session = settings_data.get(
                'model_config', {}).get('id')
            if model_id_for_session:
                self.current_model_id = model_id_for_session
            return

        if event_name == 'key_statuses_updated':
            self._load_and_refresh_keys()
            return

        if event_name == 'request_count_updated':
            key = data.get('key')
            model_id_from_event = data.get('model_id')
            count = data.get('count')
            if key and model_id_from_event == self.current_model_id and count is not None:
                self._update_key_request_count_text(key, count)
            return

        if event_name == 'fatal_error':
            source = event.get('source', '')
            if not source.startswith('worker_'):
                return

            key_to_update = event.get('worker_key', '')
            payload = event.get('data', {}).get('payload', {})
            error_type = payload.get('type')
            model_id_from_event = payload.get('model_id')

            if error_type == "temporary_pause" and model_id_from_event == self.current_model_id:
                delay = payload.get("delay", 60)
                for list_widget in [self.available_keys_list, self.active_keys_list]:
                    for i in range(list_widget.count()):
                        item = list_widget.item(i)
                        if item and item.data(QtCore.Qt.ItemDataRole.UserRole) == key_to_update:
                            item.setForeground(QtGui.QColor("#FFD700"))
                            item.setToolTip(
                                f"Полный ключ: {key_to_update}\nСтатус: Временная пауза на {delay} сек.")
                            QtCore.QTimer.singleShot(
                                delay * 1000, lambda it=item: self._reset_item_color(it))
                            return

    @pyqtSlot(int)
    def _on_provider_changed(self, index):
        provider_id = self.provider_combo.itemData(index)
        provider_display_name = self.provider_combo.itemText(index)
        self.available_keys_group.setTitle(
            f"2. Доступные ключи ({provider_display_name})")

        self._apply_provider_mode(provider_id, provider_display_name)

        self.bus.event_posted.emit({
            'event': 'provider_changed',
            'source': 'KeyManagementWidget',
            'data': {'provider_id': provider_id}
        })

        self._load_and_refresh_keys()
        self._update_server_button_visibility()

    def _update_key_request_count_text(self, key_to_update, new_count):
        for list_widget in [self.available_keys_list, self.active_keys_list]:
            for i in range(list_widget.count()):
                item = list_widget.item(i)
                if item.data(QtCore.Qt.ItemDataRole.UserRole) == key_to_update:
                    self._update_single_key_item(item)
                    return

    def _reset_item_color(self, item):
        if item:
            self._update_single_key_item(item)

    def get_selected_provider(self) -> str:
        return self.provider_combo.currentData()

    def set_session_model(self, model_id: str):
        self.current_model_id = model_id

    def _load_and_refresh_keys(self):
        # --- [PATCH START] Синхронизация UI перед обновлением ---
        # Сохраняем текущий визуальный выбор в память перед перестройкой списков.
        # Это предотвращает сброс активных ключей при внешних событиях (конец сессии/валидация).
        provider_id = self.get_selected_provider()
        if not self._provider_requires_api_key(provider_id):
            placeholder = self._provider_placeholder_key(provider_id)
            self.current_active_keys_by_provider[provider_id] = {placeholder} if placeholder else set()
            self.available_keys_list.clear()
            self.active_keys_list.clear()
            self.active_keys_list.addItem(self._create_virtual_session_item(provider_id))
            self._update_key_counts()
            self._update_active_key_count()
            return
        if self.active_keys_list.count() > 0:
            current_visual_keys = {
                self.active_keys_list.item(i).data(QtCore.Qt.ItemDataRole.UserRole)
                for i in range(self.active_keys_list.count())
            }
            self.current_active_keys_by_provider[provider_id] = current_visual_keys
        # --- [PATCH END] ---

        loaded_statuses = self.settings_manager.load_key_statuses()
        updated_statuses = []
        changed = False

        for key_info in loaded_statuses:
            if 'status_by_model' in key_info:
                for model_id in key_info['status_by_model']:
                    if not self.settings_manager.is_key_limit_active(key_info, model_id):
                        if key_info['status_by_model'][model_id].get("exhausted_at") is not None:
                            changed = True
                            key_info['status_by_model'][model_id]["exhausted_at"] = None
                            key_info['status_by_model'][model_id]["exhausted_level"] = 0
            updated_statuses.append(key_info)

        if changed:
            self.settings_manager.save_key_statuses(updated_statuses)
            updated_statuses = self.settings_manager.load_key_statuses()

        self._populate_available_keys_list(updated_statuses)

    def _create_key_list_item(self, key_info: dict) -> QtWidgets.QListWidgetItem:
        key = key_info["key"]
        request_count = self.settings_manager.get_request_count(
            key_info, self.current_model_id)
        key_short = f"…{key[-4:]}" if len(key) > 4 else key
        display_text = f"{key_short} (Запросов: {request_count})"

        item = QtWidgets.QListWidgetItem(display_text)
        item.setData(QtCore.Qt.ItemDataRole.UserRole, key)

        is_limit_active = self.settings_manager.is_key_limit_active(
            key_info, self.current_model_id)
        model_status = self.settings_manager._get_status_for_model(
            key_info, self.current_model_id)
        level = model_status.get('exhausted_level', 0)

        if not is_limit_active:
            item.setForeground(QtGui.QColor("#90EE90"))
            item.setToolTip(f"Полный ключ: {key}\nСтатус для модели: Активен.")
        elif level == 1:
            item.setForeground(QtGui.QColor("#FFD700"))
            reset_time_str = self.settings_manager.get_key_reset_time_str(
                key_info, self.current_model_id)
            item.setToolTip(
                f"Полный ключ: {key}\nСтатус для модели: Временная пауза.\n{reset_time_str}")
        else:
            item.setForeground(QtGui.QColor("#F08080"))
            reset_time_str = self.settings_manager.get_key_reset_time_str(
                key_info, self.current_model_id)
            item.setToolTip(
                f"Полный ключ: {key}\nСтатус для модели: Исчерпан.\n{reset_time_str}")

        return item

    def _populate_available_keys_list(self, current_key_statuses):
        self.available_keys_list.clear()
        self.active_keys_list.clear()

        provider_id = self.get_selected_provider()
        active_keys_for_provider = self.current_active_keys_by_provider.get(
            provider_id, set()).copy()
        demoted_keys = set()

        for key_info in current_key_statuses:
            if key_info.get('provider') == provider_id:
                key = key_info["key"]
                item = self._create_key_list_item(key_info)

                is_supposed_to_be_active = key in active_keys_for_provider
                is_red_carded = item.foreground().color().name() == "#f08080"

                if is_supposed_to_be_active and is_red_carded:
                    self.available_keys_list.addItem(item)
                    demoted_keys.add(key)
                elif is_supposed_to_be_active:
                    self.active_keys_list.addItem(item)
                else:
                    self.available_keys_list.addItem(item)

        if demoted_keys:
            active_keys_for_provider.difference_update(demoted_keys)
            self.current_active_keys_by_provider[provider_id] = active_keys_for_provider
            self.active_keys_changed.emit()

        self._update_key_counts()
        self._update_active_key_count()

    def _move_items(self, source, dest, all_items=False, filter_func=None):
        if not self._provider_requires_api_key():
            return
        items_to_move = [source.item(i) for i in range(
            source.count())] if all_items else source.selectedItems()
        if not items_to_move:
            return

        if dest == self.active_keys_list:
            good_keys, exhausted_keys = [], []
            for item in items_to_move:
                if item.foreground().color().name() == "#f08080":
                    exhausted_keys.append(item)
                else:
                    good_keys.append(item)

            for item in good_keys:
                if source.row(item) != -1:
                    dest.addItem(source.takeItem(source.row(item)))

            if exhausted_keys:
                num_exhausted = len(exhausted_keys)
                if num_exhausted == 1:
                    full_key = exhausted_keys[0].data(
                        QtCore.Qt.ItemDataRole.UserRole)
                    message_text = f"Ключ <b>…{full_key[-4:]}</b> помечен как исчерпанный."
                    informative_text = "Вы уверены, что хотите добавить его? Он может не работать."
                else:
                    message_text = f"Вы пытаетесь активировать <b>{num_exhausted}</b> ключ(ей), которые помечены как исчерпанные."
                    informative_text = "Вы уверены, что хотите добавить их все? Они могут не работать."

                msg_box = QMessageBox(self)
                msg_box.setWindowTitle("Предупреждение")
                msg_box.setIcon(QMessageBox.Icon.Warning)
                msg_box.setText(message_text)
                msg_box.setInformativeText(informative_text)
                yes_button = msg_box.addButton(
                    "Да, добавить", QMessageBox.ButtonRole.YesRole)
                no_button = msg_box.addButton(
                    "Нет, отменить", QMessageBox.ButtonRole.NoRole)
                msg_box.setDefaultButton(no_button)
                msg_box.exec()

                if msg_box.clickedButton() == yes_button:
                    for item in exhausted_keys:
                        if source.row(item) != -1:
                            dest.addItem(source.takeItem(source.row(item)))
        else:
            for item in items_to_move:
                if filter_func and not filter_func(item):
                    continue
                if source.row(item) != -1:
                    dest.addItem(source.takeItem(source.row(item)))

        provider_id = self.get_selected_provider()
        current_active_keys_set = {self.active_keys_list.item(i).data(
            QtCore.Qt.ItemDataRole.UserRole) for i in range(self.active_keys_list.count())}
        self.current_active_keys_by_provider[provider_id] = current_active_keys_set

        self._update_key_counts()
        self._update_active_key_count()
        self.active_keys_changed.emit()

    def _add_selected_to_active(self): self._move_items(
        self.available_keys_list, self.active_keys_list)

    def _remove_selected_from_active(self): self._move_items(
        self.active_keys_list, self.available_keys_list)

    def _update_single_key_item(self, item: QtWidgets.QListWidgetItem):
        try:
            if not item:
                return

            full_key = item.data(QtCore.Qt.ItemDataRole.UserRole)
            if not self._provider_requires_api_key() and full_key == self._provider_placeholder_key():
                item.setText("Встроенная браузерная сессия")
                item.setForeground(QtGui.QColor("#90EE90"))
                item.setToolTip(
                    "Этот сервис использует сохраненный браузерный профиль и не требует API-ключа."
                )
                return
            key_info = self.settings_manager.get_key_info(full_key)
            if not key_info:
                item.setText(f"…{full_key[-4:]} (удален?)")
                item.setForeground(QtGui.QColor("gray"))
                item.setToolTip(
                    f"Полный ключ: {full_key}\nСтатус: Не найден в настройках.")
                return

            request_count = self.settings_manager.get_request_count(
                key_info, self.current_model_id)
            key_short = f"…{full_key[-4:]}" if len(full_key) > 4 else full_key
            item.setText(f"{key_short} (Запросов: {request_count})")

            is_limit_active = self.settings_manager.is_key_limit_active(
                key_info, self.current_model_id)
            model_status = self.settings_manager._get_status_for_model(
                key_info, self.current_model_id)
            level = model_status.get('exhausted_level', 0)

            if not is_limit_active:
                item.setForeground(QtGui.QColor("#90EE90"))
                item.setToolTip(
                    f"Полный ключ: {full_key}\nСтатус для модели: Активен.")
            elif level == 1:
                item.setForeground(QtGui.QColor("#FFD700"))
                reset_time_str = self.settings_manager.get_key_reset_time_str(
                    key_info, self.current_model_id)
                item.setToolTip(
                    f"Полный ключ: {full_key}\nСтатус для модели: Временная пауза.\n{reset_time_str}")
            else:
                item.setForeground(QtGui.QColor("#F08080"))
                reset_time_str = self.settings_manager.get_key_reset_time_str(
                    key_info, self.current_model_id)
                item.setToolTip(
                    f"Полный ключ: {full_key}\nСтатус для модели: Исчерпан.\n{reset_time_str}")
        except RuntimeError:
            # Элемент интерфейса уже удален (C++ объект уничтожен), просто игнорируем
            pass

    def update_key_styles_for_model(self, model_id: str):
        self.current_model_id = model_id
        for list_widget in [self.available_keys_list, self.active_keys_list]:
            for i in range(list_widget.count()):
                self._update_single_key_item(list_widget.item(i))
        self._update_key_counts()

    def _add_all_to_active(self):
        if not self._provider_requires_api_key():
            return
        items_to_move = []
        for i in range(self.available_keys_list.count()):
            item = self.available_keys_list.item(i)
            if item.foreground().color().name() == "#90ee90":
                items_to_move.append(item)

        if not items_to_move:
            QMessageBox.information(
                self, "Нет активных ключей", "В пуле нет доступных 'зеленых' ключей для добавления.")
            return

        for item in list(items_to_move):
            taken_item = self.available_keys_list.takeItem(
                self.available_keys_list.row(item))
            if taken_item:
                self.active_keys_list.addItem(taken_item)

        self._update_key_counts()
        self._update_active_key_count()
        self.active_keys_changed.emit()

    def _remove_all_from_active(self): self._move_items(
        self.active_keys_list, self.available_keys_list, all_items=True)

    def _add_keys_from_text(self):
        dialog = KeyInputDialog(self)
        if not dialog.exec():
            return
        text = dialog.get_text()
        if not text.strip():
            return

        all_providers = api_config.api_providers()
        visible_providers = {
            p['display_name']: pid
            for pid, p in all_providers.items()
            if p.get('visible', True) and api_config.provider_requires_api_key(pid)
        }

        if not visible_providers:
            QMessageBox.warning(self, "Нет доступных сервисов",
                                "Нет сконфигурированных видимых API сервисов.")
            return

        dialog = ProviderChoiceDialog(list(visible_providers.keys()), self)
        if not dialog.exec():
            return

        provider_name = dialog.get_selected_item()
        provider_id = visible_providers[provider_name]

        new_keys_to_add = {k.strip() for k in text.splitlines() if k.strip()}
        added_count = self.settings_manager.add_keys_atomically(
            new_keys_to_add, provider_id)

        if added_count > 0:
            QMessageBox.information(
                self, "Ключи добавлены", f"Добавлено {added_count} новых ключей для '{provider_name}'.")
            self._load_and_refresh_keys()
        elif added_count == 0:
            QMessageBox.information(
                self, "Нет новых ключей", "Все введенные ключи уже есть в пуле.")
        else:
            QMessageBox.warning(
                self, "Ошибка", "Не удалось сохранить изменения в файле настроек.")

    def _reset_selected_statuses(self):
        selected_items = self.available_keys_list.selectedItems()
        if not selected_items:
            QMessageBox.warning(
                self, "Нет выбора", "Выберите 'красные' или 'желтые' ключи для сброса статуса.")
            return

        keys_to_reset = {item.data(QtCore.Qt.ItemDataRole.UserRole)
                         for item in selected_items}

        changed_count = 0
        for key in keys_to_reset:
            if self.settings_manager.clear_key_exhaustion_status(key, self.current_model_id):
                changed_count += 1

        if changed_count > 0:
            QMessageBox.information(
                self, "Статусы сброшены", f"Статус сброшен для {changed_count} ключ(ей).")
            self._load_and_refresh_keys()
        else:
            QMessageBox.information(
                self, "Нет изменений", "Выбранные ключи уже активны для текущей модели.")

    def _force_exhaust_selected(self):
        selected_items = self.available_keys_list.selectedItems()
        if not selected_items:
            QMessageBox.warning(
                self, "Нет выбора", "Выберите ключи в списке доступных, чтобы пометить их как исчерпанные.")
            return

        keys_to_mark = {item.data(QtCore.Qt.ItemDataRole.UserRole)
                        for item in selected_items}

        changed_count = 0
        for key in keys_to_mark:
            key_info = self.settings_manager.get_key_info(key)
            # Помечаем, только если ключ еще не помечен
            if key_info and not self.settings_manager.is_key_limit_active(key_info, self.current_model_id):
                self.settings_manager.mark_key_as_exhausted(key, self.current_model_id)
                changed_count += 1

        if changed_count > 0:
            QMessageBox.information(
                self, "Статусы обновлены", f"{changed_count} ключ(ей) помечены как исчерпанные.")
            self._load_and_refresh_keys()
        else:
            QMessageBox.information(
                self, "Нет изменений", "Все выбранные ключи уже помечены как исчерпанные для текущей модели.")


    def _handle_delete_from_pool(self):
        if not self._provider_requires_api_key():
            return
        selected_items = self.available_keys_list.selectedItems()
        if not selected_items:
            QMessageBox.information(
                self, "Нет выбора", "Выберите ключи в списке 'Доступные', которые хотите удалить.")
            return

        keys_to_remove_from_selection = {
            item.data(QtCore.Qt.ItemDataRole.UserRole) for item in selected_items}
        provider_display_name = self.provider_combo.currentText()
        current_provider_id = self.get_selected_provider()

        all_statuses = self.settings_manager.load_key_statuses()
        keys_for_provider = {ki['key'] for ki in all_statuses if ki.get(
            'provider') == current_provider_id}

        dialog = DeleteKeysDialog(
            self,
            provider_display_name,
            num_selected=len(keys_to_remove_from_selection),
            num_provider=len(keys_for_provider),
            num_total=len(all_statuses)
        )

        if not dialog.exec():
            return

        choice = dialog.choice
        keys_to_finally_remove = set()
        description = ""

        if choice == 'selected':
            keys_to_finally_remove = keys_to_remove_from_selection
            description = f"Удалено выбранных ключей: {len(keys_to_finally_remove)}"
        elif choice == 'provider':
            keys_to_finally_remove = keys_for_provider
            description = f"Удалены все ключи для '{provider_display_name}': {len(keys_to_finally_remove)}"
        elif choice == 'all':
            keys_to_finally_remove = {ki['key'] for ki in all_statuses}
            description = f"Удалены ВСЕ ключи из пула: {len(keys_to_finally_remove)}"

        if not keys_to_finally_remove:
            return

        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("Подтверждение удаления")
        msg_box.setIcon(QMessageBox.Icon.Warning)
        msg_box.setText(
            f"Вы уверены, что хотите НАВСЕГДА удалить {len(keys_to_finally_remove)} ключ(ей)?")
        msg_box.setInformativeText("Это действие необратимо.")

        yes_button = msg_box.addButton(
            "Да, удалить", QMessageBox.ButtonRole.YesRole)
        no_button = msg_box.addButton("Нет", QMessageBox.ButtonRole.NoRole)
        msg_box.setDefaultButton(no_button)

        msg_box.exec()

        if msg_box.clickedButton() == yes_button:
            QtCore.QTimer.singleShot(
                10,
                lambda: self.show_del_warn(keys_to_finally_remove, description)
            )

    def _update_server_button_visibility(self):
        if not self.server_manager:
            self.server_button.setVisible(False)
            return

        provider_id = self.provider_combo.currentData()
        provider_config = api_config.api_providers().get(provider_id, {})
        
        # --- НОВАЯ ЛОГИКА ---
        # Кнопка видна, если у провайдера указан класс сервера
        has_server = "server_class" in provider_config
        
        self.server_button.setVisible(has_server)

        if has_server:
            is_running = self.server_manager.is_server_running()
            message = "running" if is_running else None
            self._update_server_button(is_running, message)

    def _update_server_button(self, is_running, message):
        if not self.server_manager:
            return

        provider_id = self.provider_combo.currentData()
        is_direct = provider_id == 'perplexytiApiMOD'

        if not is_direct:
            return

        if is_running:
            self.server_button.setText("Остановить сервер")
            self.server_button.setStyleSheet("""
                QPushButton { background-color: #c62828; color: white; font-weight: bold; padding: 10px; border-radius: 4px; }
                QPushButton:hover { background-color: #d32f2f; }
                QPushButton:disabled { background-color: #5c1818; color: #888; }
            """)
            self.server_button.setToolTip(
                "Сервер Perplexity запущен. Нажмите для остановки.")
        elif message == "error":
            self.server_button.setText("Ошибка сервера")
            self.server_button.setStyleSheet("""
                QPushButton { background-color: #546e7a; color: white; padding: 10px; border-radius: 4px; }
                QPushButton:hover { background-color: #607d8b; }
                QPushButton:disabled { background-color: #37474f; color: #888; }
            """)
            self.server_button.setToolTip(
                "Ошибка сервера Perplexity. Проверьте логи.")
        else:
            self.server_button.setText("Запустить сервер")
            self.server_button.setStyleSheet("""
                QPushButton { background-color: #2e7d32; color: white; font-weight: bold; padding: 10px; border-radius: 4px; }
                QPushButton:hover { background-color: #388e3c; }
                QPushButton:disabled { background-color: #1b5e20; color: #888; }
            """)
            self.server_button.setToolTip(
                "Сервер Perplexity остановлен. Нажмите для запуска.")

    def show_del_warn(self, keys_to_finally_remove, description):
        removed_count = self.settings_manager.remove_keys_atomically(
            keys_to_finally_remove)

        if removed_count > 0:
            QMessageBox.information(self, "Выполнено", description)
        elif removed_count == 0:
            QMessageBox.information(
                self, "Нет изменений", "Выбранные ключи не были найдены в пуле. Возможно, они уже были удалены.")
        else:
            QMessageBox.warning(
                self, "Ошибка", "Не удалось сохранить изменения в файле настроек.")
