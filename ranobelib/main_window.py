import logging
import os
from datetime import datetime, timedelta

from PyQt6.QtCore import QDateTime, QSettings, Qt, QUrl
from PyQt6.QtGui import (
    QAction,
    QDesktopServices,
    QDragEnterEvent,
    QDropEvent,
    QKeySequence,
    QShortcut,
)
from PyQt6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSystemTrayIcon,
    QTextEdit,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from constants import (
    APP_NAME,
    APP_VERSION,
    DARK_STYLE,
    LIGHT_STYLE,
    LOG_DIR,
    SETTINGS_APP,
    SETTINGS_ORG,
)
from api_upload import ApiUploadWorker
from dialogs import PreviewDialog, ProcessDialog
from models import ChapterData
from parsers import FileParser
from utils import format_num, validate_rulate_url, validate_url
from workers import (
    LastChapterDetector,
    LoginWorker,
    RulateDownloadWorker,
    UploadWorker,
)

class RanobeUploaderApp(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        self.resize(900, 1050)
        self.setAcceptDrops(True)

        self.chapters_to_upload: list[ChapterData] = []
        self.last_clicked_row = -1
        self.rulate_last_clicked_row = -1
        self.settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
        self._is_dark = False
        self._current_file_path = ""  # Feature 2: путь текущего файла
        self._upload_ok = 0           # Feature 4: счётчик для уведомлений
        self._upload_errors = 0       # Feature 4: счётчик для уведомлений
        self._rulate_chapter_list = []  # v12: список глав с rulate
        self._last_lib_chapter = 0.0    # v12: номер последней главы на lib
        self._process_dialogs: dict[str, ProcessDialog] = {}
        self._return_to_menu_handler = None

        self._build_ui()
        self._setup_tray_icon()
        self._restore_settings()

    # ── Построение интерфейса ──

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setSpacing(6)

        # Toolbar
        toolbar = QToolBar()
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        self.act_return_to_menu = QAction("← В меню", self)
        self.act_return_to_menu.triggered.connect(self._return_to_menu)
        toolbar.addAction(self.act_return_to_menu)
        toolbar.addSeparator()

        self.act_dark = QAction("🌙 Тёмная тема", self, checkable=True)
        self.act_dark.toggled.connect(self._toggle_theme)
        toolbar.addAction(self.act_dark)

        act_export_log = QAction("📋 Экспорт лога", self)
        act_export_log.triggered.connect(self._export_log)
        toolbar.addAction(act_export_log)

        act_open_log_dir = QAction("📂 Папка логов", self)
        act_open_log_dir.triggered.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(LOG_DIR.resolve())))
        )
        toolbar.addAction(act_open_log_dir)

        # ── Feature 1: Профили ──
        toolbar.addSeparator()
        toolbar.addWidget(QLabel(" Профиль: "))
        self.profile_combo = QComboBox()
        self.profile_combo.setMinimumWidth(150)
        self.profile_combo.addItem("(Текущий)")
        self.profile_combo.currentIndexChanged.connect(self._on_profile_selected)
        toolbar.addWidget(self.profile_combo)

        self.btn_save_profile = QPushButton("Сохранить профиль")
        self.btn_save_profile.clicked.connect(self._save_profile)
        toolbar.addWidget(self.btn_save_profile)

        self.btn_delete_profile = QPushButton("Удалить профиль")
        self.btn_delete_profile.clicked.connect(self._delete_profile)
        toolbar.addWidget(self.btn_delete_profile)

        # StatusBar
        self.statusBar().showMessage("Готов")
        self.lbl_stats = QLabel("OK: 0  Ошибки: 0  Пропущено: 0")
        self.statusBar().addPermanentWidget(self.lbl_stats)

        # 1. URL RanobeLib
        layout.addWidget(QLabel("1. URL страницы добавления глав (RanobeLib):"))
        url_row = QHBoxLayout()
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText(
            "https://ranobelib.me/ru/book/.../add-chapter"
        )
        url_row.addWidget(self.url_input)

        self.btn_login = QPushButton("Войти в RanobeLib")
        self.btn_login.clicked.connect(self._start_login)
        self.btn_login.setStyleSheet(
            "background-color: #FFA500; font-weight: bold; padding: 6px 16px;"
        )
        url_row.addWidget(self.btn_login)
        layout.addLayout(url_row)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Способ загрузки:"))
        self.upload_mode_combo = QComboBox()
        self.upload_mode_combo.addItem("Через браузер", "browser")
        self.upload_mode_combo.addItem("Через API (альтернативно)", "api")
        self.upload_mode_combo.setToolTip(
            "Браузерный режим повторяет текущую автоматизацию через Playwright.\n"
            "API-режим использует сохранённую авторизацию RanobeLib и загружает главы напрямую."
        )
        self.upload_mode_combo.currentIndexChanged.connect(self._on_upload_mode_changed)
        mode_row.addWidget(self.upload_mode_combo)
        mode_row.addStretch()
        layout.addLayout(mode_row)

        # ══════════════════════════════════════════════════════════════
        #  v12: СЕКЦ�?Я RULATE
        # ══════════════════════════════════════════════════════════════
        rulate_group = QGroupBox("Скачать главы с Rulate")
        rulate_layout = QVBoxLayout()

        # URL Rulate
        rulate_url_row = QHBoxLayout()
        rulate_url_row.addWidget(QLabel("URL книги на Rulate:"))
        self.rulate_url_input = QLineEdit()
        self.rulate_url_input.setPlaceholderText("https://tl.rulate.ru/book/123870")
        rulate_url_row.addWidget(self.rulate_url_input)

        self.btn_login_rulate = QPushButton("Войти в Rulate (опционально)")
        self.btn_login_rulate.setToolTip(
            "Авторизация нужна только для скачивания платных глав.\n"
            "Бесплатные главы скачиваются без входа в аккаунт."
        )
        self.btn_login_rulate.clicked.connect(self._start_login_rulate)
        self.btn_login_rulate.setStyleSheet(
            "background-color: #FFA500; padding: 5px 10px;"
        )
        rulate_url_row.addWidget(self.btn_login_rulate)
        rulate_layout.addLayout(rulate_url_row)

        # Опции Rulate
        rulate_opts_row = QHBoxLayout()

        self.chk_skip_uploaded = QCheckBox("Пропустить залитые на RanobeLib")
        self.chk_skip_uploaded.setToolTip(
            "Определит последнюю главу на RanobeLib и покажет только новые главы с Rulate"
        )
        self.chk_skip_uploaded.setChecked(True)
        rulate_opts_row.addWidget(self.chk_skip_uploaded)

        rulate_opts_row.addStretch()

        self.btn_fetch_rulate = QPushButton("Получить список глав")
        self.btn_fetch_rulate.clicked.connect(self._fetch_rulate_chapters)
        self.btn_fetch_rulate.setStyleSheet(
            "background-color: #87CEEB; font-weight: bold; padding: 6px 16px;"
        )
        rulate_opts_row.addWidget(self.btn_fetch_rulate)

        self.btn_download_rulate = QPushButton("Скачать выбранные главы")
        self.btn_download_rulate.clicked.connect(self._download_rulate_chapters)
        self.btn_download_rulate.setStyleSheet(
            "background-color: #90EE90; font-weight: bold; padding: 6px 16px;"
        )
        self.btn_download_rulate.setEnabled(False)
        rulate_opts_row.addWidget(self.btn_download_rulate)

        rulate_layout.addLayout(rulate_opts_row)

        # �?нформация о последней главе
        self.lbl_last_chapter = QLabel("Последняя глава на RanobeLib: —")
        rulate_layout.addWidget(self.lbl_last_chapter)

        # Список глав Rulate (для выбора)
        self.rulate_list_widget = QListWidget()
        self.rulate_list_widget.setMaximumHeight(200)
        self.rulate_list_widget.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.rulate_list_widget.itemClicked.connect(self._on_rulate_item_clicked)
        rulate_layout.addWidget(self.rulate_list_widget)
        rulate_layout.addWidget(QLabel("Подсказка: Shift+клик — выделение диапазона глав"))

        # Кнопки выбора для Rulate
        rulate_btns_row = QHBoxLayout()
        btn_rulate_sel_all = QPushButton("Все")
        btn_rulate_sel_all.clicked.connect(lambda: self._rulate_select(True))
        btn_rulate_desel_all = QPushButton("Ничего")
        btn_rulate_desel_all.clicked.connect(lambda: self._rulate_select(False))
        rulate_btns_row.addWidget(btn_rulate_sel_all)
        rulate_btns_row.addWidget(btn_rulate_desel_all)
        rulate_btns_row.addStretch()
        self.lbl_rulate_info = QLabel("")
        rulate_btns_row.addWidget(self.lbl_rulate_info)
        rulate_layout.addLayout(rulate_btns_row)

        rulate_group.setLayout(rulate_layout)
        layout.addWidget(rulate_group)

        # ══════════════════════════════════════════════════════════════

        # 2. Том + номер
        vol_row = QHBoxLayout()
        vol_row.addWidget(QLabel("2. Том (по умолч.):"))
        self.default_vol_input = QLineEdit("1")
        self.default_vol_input.setFixedWidth(50)
        vol_row.addWidget(self.default_vol_input)
        self.chk_force_num = QCheckBox("Вписывать номер главы из файла")
        vol_row.addWidget(self.chk_force_num)
        vol_row.addStretch()
        layout.addLayout(vol_row)

        # 3. Расписание
        schedule_group = QGroupBox("3. Отложенная публикация")
        sched_layout = QVBoxLayout()
        self.chk_schedule = QCheckBox("Включить отложку")
        self.chk_schedule.stateChanged.connect(self._toggle_schedule)
        sched_layout.addWidget(self.chk_schedule)

        self.schedule_widget = QWidget()
        sw_layout = QHBoxLayout(self.schedule_widget)
        sw_layout.setContentsMargins(0, 0, 0, 0)
        sw_layout.addWidget(QLabel("Старт:"))
        self.date_edit = QDateTimeEdit(QDateTime.currentDateTime())
        self.date_edit.setDisplayFormat("dd.MM.yyyy HH:mm")
        self.date_edit.setCalendarPopup(True)
        sw_layout.addWidget(self.date_edit)
        sw_layout.addWidget(QLabel("Интервал (мин):"))
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(1, 99999)
        self.interval_spin.setValue(1440)
        sw_layout.addWidget(self.interval_spin)
        self.schedule_widget.setEnabled(False)
        sched_layout.addWidget(self.schedule_widget)
        schedule_group.setLayout(sched_layout)
        layout.addWidget(schedule_group)

        # 4. Платный доступ
        paid_group = QGroupBox("4. Платный доступ")
        paid_layout = QHBoxLayout()
        self.chk_paid = QCheckBox("Платные главы")
        self.chk_paid.stateChanged.connect(self._toggle_paid)
        paid_layout.addWidget(self.chk_paid)
        paid_layout.addWidget(QLabel("Цена (₽):"))
        self.spin_price = QSpinBox()
        self.spin_price.setRange(1, 10000)
        self.spin_price.setValue(10)
        self.spin_price.setEnabled(False)
        paid_layout.addWidget(self.spin_price)
        paid_layout.addStretch()
        paid_group.setLayout(paid_layout)
        layout.addWidget(paid_group)

        # 5. Файл (или загруженные из Rulate)
        file_row = QHBoxLayout()
        self.btn_file = QPushButton("5. Выбрать файл (.epub / .zip / .txt / .html)")
        self.btn_file.clicked.connect(self._select_file)
        self.btn_file.setStyleSheet(
            "background-color: #ADD8E6; font-weight: bold; padding: 6px 16px;"
        )
        file_row.addWidget(self.btn_file)
        self.lbl_info = QLabel("Файл не выбран (или скачайте из Rulate)")
        file_row.addWidget(self.lbl_info)
        layout.addLayout(file_row)

        # 6. Список глав
        chapters_group = QGroupBox("Список глав для загрузки на RanobeLib (ПКМ → меню, Shift+клик — диапазон)")
        ch_layout = QVBoxLayout()

        btns_row = QHBoxLayout()
        self.btn_sel_all = QPushButton("Все")
        self.btn_sel_all.clicked.connect(self._select_all)
        self.btn_desel_all = QPushButton("Ничего")
        self.btn_desel_all.clicked.connect(self._deselect_all)
        self.btn_invert = QPushButton("Инвертировать")
        self.btn_invert.clicked.connect(self._invert_selection)
        btns_row.addWidget(self.btn_sel_all)
        btns_row.addWidget(self.btn_desel_all)
        btns_row.addWidget(self.btn_invert)
        btns_row.addStretch()
        ch_layout.addLayout(btns_row)

        # Feature 3: Поиск по главам
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Поиск по названию или номеру главы…")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self._filter_chapters)
        ch_layout.addWidget(self.search_input)

        self.chapters_list_widget = QListWidget()
        self.chapters_list_widget.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.chapters_list_widget.itemClicked.connect(self._on_item_clicked)
        self.chapters_list_widget.itemDoubleClicked.connect(self._on_item_dblclick)
        self.chapters_list_widget.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.chapters_list_widget.customContextMenuRequested.connect(
            self._on_context_menu
        )
        ch_layout.addWidget(self.chapters_list_widget)

        chapters_group.setLayout(ch_layout)
        chapters_group.setEnabled(False)
        self.chapters_group = chapters_group
        layout.addWidget(chapters_group)

        # Прогресс + ETA + уведомления
        prog_row = QHBoxLayout()
        self.progress_bar = QProgressBar()
        prog_row.addWidget(self.progress_bar, stretch=1)
        self.lbl_eta = QLabel("ETA: —")
        self.lbl_eta.setFixedWidth(120)
        prog_row.addWidget(self.lbl_eta)
        # Feature 4: чекбокс уведомлений
        self.chk_notify = QCheckBox("Уведомлять о завершении")
        self.chk_notify.setChecked(True)
        prog_row.addWidget(self.chk_notify)
        layout.addLayout(prog_row)

        # Лог
        self.log_area = QTextEdit()
        self.log_area.setObjectName("logArea")
        self.log_area.setReadOnly(True)
        layout.addWidget(self.log_area)

        # Кнопки
        btn_row = QHBoxLayout()
        self.btn_start = QPushButton("СТАРТ ЗАГРУЗКИ НА RANOBELIB")
        self.btn_start.clicked.connect(self._start_upload)
        self.btn_start.setEnabled(False)
        self.btn_start.setStyleSheet(
            "background-color: #90EE90; min-height: 40px; font-weight: bold; font-size: 14px;"
        )
        self.btn_stop = QPushButton("СТОП")
        self.btn_stop.clicked.connect(self._stop_upload)
        self.btn_stop.setEnabled(False)
        self.btn_stop.setStyleSheet(
            "background-color: #FFB6C1; min-height: 40px; font-size: 14px;"
        )
        btn_row.addWidget(self.btn_start)
        btn_row.addWidget(self.btn_stop)
        layout.addLayout(btn_row)

        # Шорткаты
        QShortcut(QKeySequence("Ctrl+A"), self).activated.connect(self._select_all)
        QShortcut(QKeySequence("Ctrl+O"), self).activated.connect(self._select_file)

        # Применить начальную тему
        self._apply_theme()
        self._append_log(
            "INFO",
            f"Готов. v{APP_VERSION} — .epub, .zip(docx), .txt, .md, .html + Rulate + API",
        )

    # ── Feature 4: Системный трей ──

    def _setup_tray_icon(self):
        """�?нициализация иконки в системном трее для уведомлений."""
        self._tray_icon = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self._tray_icon = QSystemTrayIcon(self)
            # �?спользуем иконку приложения или стандартную
            app_icon = self.windowIcon()
            if not app_icon.isNull():
                self._tray_icon.setIcon(app_icon)
            else:
                self._tray_icon.setIcon(self.style().standardIcon(
                    self.style().StandardPixmap.SP_ComputerIcon
                ))
            self._tray_icon.setToolTip(f"{APP_NAME} v{APP_VERSION}")
            self._tray_icon.show()

    def _show_notification(self, title: str, message: str):
        """Показать уведомление через системный трей или QMessageBox."""
        if not self.chk_notify.isChecked():
            return

        QApplication.beep()

        if self._tray_icon and QSystemTrayIcon.isSystemTrayAvailable():
            self._tray_icon.showMessage(
                title,
                message,
                QSystemTrayIcon.MessageIcon.Information,
                5000,
            )
        else:
            QMessageBox.information(self, title, message)

    def set_return_to_menu_handler(self, handler):
        self._return_to_menu_handler = handler

    def _return_to_menu(self):
        self._save_settings()
        if callable(self._return_to_menu_handler):
            self.hide()
            self.close()
            self._return_to_menu_handler()
            return
        self.close()

    # ── Feature 1: Профили ──

    def _load_profile_list(self):
        """Загрузить список профилей из QSettings в комбобокс."""
        self.profile_combo.blockSignals(True)
        current_text = self.profile_combo.currentText()
        self.profile_combo.clear()
        self.profile_combo.addItem("(Текущий)")

        self.settings.beginGroup("profiles")
        names = self.settings.childGroups()
        self.settings.endGroup()

        for name in sorted(names):
            self.profile_combo.addItem(name)

        # Восстановить выбор, если профиль ещё существует
        idx = self.profile_combo.findText(current_text)
        if idx >= 0:
            self.profile_combo.setCurrentIndex(idx)

        self.profile_combo.blockSignals(False)

    def _save_profile(self):
        """Сохранить текущие настройки как именованный профиль."""
        name, ok = QInputDialog.getText(
            self, "Сохранить профиль", "Имя профиля:"
        )
        if not ok or not name.strip():
            return
        name = name.strip()

        if name == "(Текущий)":
            QMessageBox.warning(self, "Ошибка", "Нельзя использовать зарезервированное имя.")
            return

        self.settings.beginGroup(f"profiles/{name}")
        self.settings.setValue("url", self.url_input.text())
        self.settings.setValue("upload_mode", self._current_upload_mode())
        self.settings.setValue("default_vol", self.default_vol_input.text())
        self.settings.setValue("force_num", self.chk_force_num.isChecked())
        self.settings.setValue("schedule", self.chk_schedule.isChecked())
        self.settings.setValue("start_time", self.date_edit.dateTime())
        self.settings.setValue("interval", self.interval_spin.value())
        self.settings.setValue("paid", self.chk_paid.isChecked())
        self.settings.setValue("price", self.spin_price.value())
        self.settings.setValue("rulate_url", self.rulate_url_input.text())
        self.settings.endGroup()

        self._load_profile_list()
        idx = self.profile_combo.findText(name)
        if idx >= 0:
            self.profile_combo.setCurrentIndex(idx)

        self._append_log("INFO", f"Профиль «{name}» сохранён.")

    def _delete_profile(self):
        """Удалить выбранный профиль."""
        name = self.profile_combo.currentText()
        if name == "(Текущий)":
            QMessageBox.warning(self, "Ошибка", "Нельзя удалить текущий (несохранённый) профиль.")
            return

        answer = QMessageBox.question(
            self, "Удаление профиля",
            f"Удалить профиль «{name}»?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        self.settings.beginGroup(f"profiles/{name}")
        self.settings.remove("")
        self.settings.endGroup()

        self._load_profile_list()
        self.profile_combo.setCurrentIndex(0)
        self._append_log("INFO", f"Профиль «{name}» удалён.")

    def _on_profile_selected(self, index: int):
        """Загрузить настройки выбранного профиля в UI."""
        name = self.profile_combo.currentText()
        if name == "(Текущий)" or index <= 0:
            return

        self.settings.beginGroup(f"profiles/{name}")
        url = self.settings.value("url", "")
        if url:
            self.url_input.setText(url)
        upload_mode = self.settings.value("upload_mode", "browser")
        upload_mode_index = self.upload_mode_combo.findData(upload_mode)
        if upload_mode_index >= 0:
            self.upload_mode_combo.setCurrentIndex(upload_mode_index)
        vol = self.settings.value("default_vol", "1")
        self.default_vol_input.setText(vol)
        self.chk_force_num.setChecked(self.settings.value("force_num", False, type=bool))
        self.chk_schedule.setChecked(self.settings.value("schedule", False, type=bool))
        start_time = self.settings.value("start_time")
        if start_time is not None:
            self.date_edit.setDateTime(start_time)
        self.interval_spin.setValue(self.settings.value("interval", 1440, type=int))
        self.chk_paid.setChecked(self.settings.value("paid", False, type=bool))
        self.spin_price.setValue(self.settings.value("price", 10, type=int))
        rulate_url = self.settings.value("rulate_url", "")
        if rulate_url:
            self.rulate_url_input.setText(rulate_url)
        self.settings.endGroup()
        self._on_upload_mode_changed(self.upload_mode_combo.currentIndex())

        self._append_log("INFO", f"Профиль «{name}» загружен.")

    # ── Feature 3: Поиск / фильтрация глав ──

    def _filter_chapters(self, text: str):
        """Скрыть/показать элементы списка глав по поисковому запросу."""
        query = text.strip().lower()
        for i in range(self.chapters_list_widget.count()):
            item = self.chapters_list_widget.item(i)
            if not query:
                item.setHidden(False)
                continue
            chapter: ChapterData = item.data(Qt.ItemDataRole.UserRole)
            # Сопоставление по: заголовку, номеру главы, номеру тома
            searchable = f"{chapter.title} {format_num(chapter.number)} {chapter.volume}".lower()
            item.setHidden(query not in searchable)

    # ── Тема ──

    def _toggle_theme(self, dark: bool):
        self._is_dark = dark
        self._apply_theme()
        self.settings.setValue("dark_theme", dark)

    def _apply_theme(self):
        self.setStyleSheet(DARK_STYLE if self._is_dark else LIGHT_STYLE)

    # ── Drag & Drop ──

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                path = url.toLocalFile().lower()
                if any(path.endswith(ext) for ext in (".epub", ".zip", ".txt", ".md", ".html", ".htm")):
                    event.acceptProposedAction()
                    return

    def dropEvent(self, event: QDropEvent):
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path:
                self._load_file(path)
                break

    # ── Сохранение / восстановление настроек ──

    def _restore_settings(self):
        url = self.settings.value("url", "")
        if url:
            self.url_input.setText(url)
        upload_mode = self.settings.value("upload_mode", "browser")
        upload_mode_index = self.upload_mode_combo.findData(upload_mode)
        if upload_mode_index >= 0:
            self.upload_mode_combo.setCurrentIndex(upload_mode_index)
        vol = self.settings.value("default_vol", "1")
        self.default_vol_input.setText(vol)
        self.chk_force_num.setChecked(self.settings.value("force_num", False, type=bool))
        self.chk_schedule.setChecked(self.settings.value("schedule", False, type=bool))
        self.interval_spin.setValue(self.settings.value("interval", 1440, type=int))
        self.chk_paid.setChecked(self.settings.value("paid", False, type=bool))
        self.spin_price.setValue(self.settings.value("price", 10, type=int))
        self._is_dark = self.settings.value("dark_theme", False, type=bool)
        self.act_dark.setChecked(self._is_dark)
        self._apply_theme()
        rulate_url = self.settings.value("rulate_url", "")
        if rulate_url:
            self.rulate_url_input.setText(rulate_url)
        self.chk_skip_uploaded.setChecked(
            self.settings.value("skip_uploaded", True, type=bool)
        )
        # Feature 1: загрузить список профилей
        self._load_profile_list()
        self._on_upload_mode_changed(self.upload_mode_combo.currentIndex())

    def _save_settings(self):
        self.settings.setValue("url", self.url_input.text())
        self.settings.setValue("upload_mode", self._current_upload_mode())
        self.settings.setValue("default_vol", self.default_vol_input.text())
        self.settings.setValue("force_num", self.chk_force_num.isChecked())
        self.settings.setValue("schedule", self.chk_schedule.isChecked())
        self.settings.setValue("interval", self.interval_spin.value())
        self.settings.setValue("paid", self.chk_paid.isChecked())
        self.settings.setValue("price", self.spin_price.value())
        self.settings.setValue("dark_theme", self._is_dark)
        self.settings.setValue("rulate_url", self.rulate_url_input.text())
        self.settings.setValue("skip_uploaded", self.chk_skip_uploaded.isChecked())

    def closeEvent(self, event):
        self._save_settings()
        for dlg in self._process_dialogs.values():
            try:
                dlg.close()
            except Exception:
                pass
        # Скрыть трей-иконку при выходе
        if self._tray_icon:
            self._tray_icon.hide()
        super().closeEvent(event)

    # ── Переключатели ──

    def _toggle_schedule(self, state):
        self.schedule_widget.setEnabled(state == 2)

    def _toggle_paid(self, state):
        self.spin_price.setEnabled(state == 2 and self._current_upload_mode() != "api")

    def _current_upload_mode(self) -> str:
        return self.upload_mode_combo.currentData() or "browser"

    def _on_upload_mode_changed(self, _index: int):
        is_api_mode = self._current_upload_mode() == "api"
        if is_api_mode:
            self.chk_paid.setChecked(False)
        self.chk_paid.setEnabled(not is_api_mode)
        self.spin_price.setEnabled(self.chk_paid.isChecked() and not is_api_mode)

        paid_tooltip = (
            "Платные главы пока поддерживаются только в браузерном режиме."
            if is_api_mode
            else ""
        )
        self.chk_paid.setToolTip(paid_tooltip)
        self.spin_price.setToolTip(paid_tooltip)

    def _open_process_dialog(self, key: str, title: str, can_stop: bool = False, stop_fn=None):
        dlg = self._process_dialogs.get(key)
        if dlg is None:
            dlg = ProcessDialog(title, can_stop=can_stop, parent=self)
            self._process_dialogs[key] = dlg
            if stop_fn:
                dlg.stop_requested.connect(stop_fn)
        dlg.setWindowTitle(title)
        dlg.progress.setValue(0)
        dlg.log_view.clear()
        dlg.btn_close.setEnabled(False)
        if dlg.btn_stop:
            dlg.btn_stop.setEnabled(True)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()
        return dlg

    def _process_log(self, key: str, level: str, message: str):
        self._append_log(level, message)
        dlg = self._process_dialogs.get(key)
        if dlg:
            dlg.append_log(level, message)

    def _process_progress(self, key: str, value: int):
        self.progress_bar.setValue(value)
        dlg = self._process_dialogs.get(key)
        if dlg:
            dlg.set_progress(value)

    def _finish_process_dialog(self, key: str):
        dlg = self._process_dialogs.get(key)
        if dlg:
            dlg.mark_finished()

    # ── Логирование ──

    def _append_log(self, level: str, message: str):
        if level == "ERROR":
            logging.error(message)
        elif level == "WARNING":
            logging.warning(message)
        else:
            logging.info(message)

        ts = datetime.now().strftime("%H:%M:%S")
        colors = {
            "ERROR": "#D32F2F",
            "WARNING": "#F57C00",
            "SUCCESS": "#388E3C",
        }
        color = colors.get(level, "#888" if self._is_dark else "#000")

        self.log_area.append(
            f'<span style="color:#888;">[{ts}]</span> '
            f'<span style="color:{color}; font-weight:bold;">{message}</span>'
        )
        sb = self.log_area.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _export_log(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить лог", f"log_{datetime.now():%Y-%m-%d_%H%M}.txt",
            "Text (*.txt)"
        )
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.log_area.toPlainText())
            self._append_log("INFO", f"Лог сохранён: {path}")

    # ── Авторизация ──

    def _start_login(self):
        self._open_process_dialog("login_lib", "RanobeLib: авторизация")
        self.login_worker = LoginWorker(site="ranobelib")
        self.login_worker.log_signal.connect(
            lambda level, msg: self._process_log("login_lib", level, msg)
        )
        self.login_worker.finished_signal.connect(
            lambda: (self.btn_login.setEnabled(True), self._finish_process_dialog("login_lib"))
        )
        self.btn_login.setEnabled(False)
        self.login_worker.start()

    def _start_login_rulate(self):
        self._open_process_dialog("login_rulate", "Rulate: авторизация")
        self.login_rulate_worker = LoginWorker(site="rulate")
        self.login_rulate_worker.log_signal.connect(
            lambda level, msg: self._process_log("login_rulate", level, msg)
        )
        self.login_rulate_worker.finished_signal.connect(
            lambda: (self.btn_login_rulate.setEnabled(True), self._finish_process_dialog("login_rulate"))
        )
        self.btn_login_rulate.setEnabled(False)
        self.login_rulate_worker.start()

    # ══════════════════════════════════════════════════════════════════
    #  v12: RULATE — получение списка глав
    # ══════════════════════════════════════════════════════════════════

    def _fetch_rulate_chapters(self):
        """Получить список глав с Rulate и (опционально) определить последнюю на Lib."""
        rulate_url = self.rulate_url_input.text().strip()
        if not rulate_url:
            return QMessageBox.warning(self, "Ошибка", "Введите URL книги на Rulate.")

        if not validate_rulate_url(rulate_url):
            answer = QMessageBox.question(
                self,
                "Подозрительный URL",
                f"URL не соответствует шаблону tl.rulate.ru/book/XXXXX.\n\n"
                f"{rulate_url}\n\nВсё равно продолжить?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.No:
                return

        self._open_process_dialog("rulate_fetch", "Rulate: получение списка глав")
        self.btn_fetch_rulate.setEnabled(False)
        self.btn_download_rulate.setEnabled(False)
        self.rulate_list_widget.clear()
        self.rulate_last_clicked_row = -1
        self._rulate_chapter_list = []
        self.progress_bar.setValue(0)

        # Если нужно определить последнюю главу на RanobeLib
        if self.chk_skip_uploaded.isChecked() and self.url_input.text().strip():
            self._process_log("rulate_fetch", "INFO", "Определяю последнюю залитую главу на RanobeLib…")
            self._detector = LastChapterDetector(self.url_input.text().strip())
            self._detector.log_signal.connect(
                lambda level, msg: self._process_log("rulate_fetch", level, msg)
            )
            self._detector.result_signal.connect(self._on_last_chapter_detected)
            self._detector.finished_signal.connect(self._on_detector_finished)
            self._detector.start()
        else:
            self._last_lib_chapter = 0.0
            self.lbl_last_chapter.setText("Последняя глава на RanobeLib: — (пропуск отключён)")
            self._start_rulate_list_fetch()

    def _on_last_chapter_detected(self, num: float, desc: str):
        """Получен номер последней главы с RanobeLib."""
        self._last_lib_chapter = num
        if num > 0:
            self.lbl_last_chapter.setText(
                f"Последняя глава на RanobeLib: {format_num(num)} ({desc})"
            )
        else:
            self.lbl_last_chapter.setText("Последняя глава на RanobeLib: не определена")

    def _on_detector_finished(self):
        """Детектор завершён — запускаем загрузку списка с Rulate."""
        self._start_rulate_list_fetch()

    def _start_rulate_list_fetch(self):
        """Запустить получение списка глав с Rulate."""
        rulate_url = self.rulate_url_input.text().strip()
        vol = self.default_vol_input.text().strip() or "1"

        self._rulate_worker = RulateDownloadWorker(
            rulate_url=rulate_url,
            default_vol=vol,
            skip_after=int(self._last_lib_chapter),
        )
        self._rulate_worker.log_signal.connect(
            lambda level, msg: self._process_log("rulate_fetch", level, msg)
        )
        self._rulate_worker.progress_signal.connect(
            lambda val: self._process_progress("rulate_fetch", val)
        )
        self._rulate_worker.chapter_list_ready.connect(self._on_rulate_list_ready)
        self._rulate_worker.finished_signal.connect(self._on_rulate_list_finished)
        self._rulate_worker.start()

    def _on_rulate_list_ready(self, chapters_info: list):
        """Список глав с Rulate получен — заполняем виджет."""
        self._rulate_chapter_list = chapters_info
        self.rulate_list_widget.clear()
        self.rulate_last_clicked_row = -1

        skip_num = self._last_lib_chapter
        total = 0
        downloadable = 0
        skipped = 0

        for ch in chapters_info:
            title = ch.get("title", "")
            ch_num = ch.get("number", 0)
            can_download = ch.get("downloadable", False)
            ch_id = ch.get("id", "")

            item = QListWidgetItem(title)
            item.setData(Qt.ItemDataRole.UserRole, ch)

            # Если глава уже залита — снимаем галочку
            if skip_num > 0 and ch_num > 0 and ch_num <= skip_num:
                item.setCheckState(Qt.CheckState.Unchecked)
                skipped += 1
            elif can_download:
                item.setCheckState(Qt.CheckState.Checked)
                downloadable += 1
            else:
                item.setCheckState(Qt.CheckState.Unchecked)

            if not can_download:
                item.setForeground(Qt.GlobalColor.gray)
                item.setToolTip("Недоступна для скачивания (требуется подписка)")

            self.rulate_list_widget.addItem(item)
            total += 1

        info_parts = [f"Всего: {total}"]
        if skipped:
            info_parts.append(f"пропущено (залиты): {skipped}")
        info_parts.append(f"к скачиванию: {downloadable}")
        self.lbl_rulate_info.setText(" | ".join(info_parts))
        self.btn_download_rulate.setEnabled(downloadable > 0)

    def _on_rulate_list_finished(self):
        self.btn_fetch_rulate.setEnabled(True)
        self._finish_process_dialog("rulate_fetch")

    def _rulate_select(self, select: bool):
        """Выбрать/снять все доступные главы в списке Rulate."""
        for i in range(self.rulate_list_widget.count()):
            item = self.rulate_list_widget.item(i)
            ch = item.data(Qt.ItemDataRole.UserRole)
            if ch and ch.get("downloadable", False):
                item.setCheckState(
                    Qt.CheckState.Checked if select else Qt.CheckState.Unchecked
                )

    def _on_rulate_item_clicked(self, item):
        """Shift+клик: массовое выделение диапазона в списке Rulate."""
        row = self.rulate_list_widget.row(item)
        mods = QApplication.keyboardModifiers()

        if (mods & Qt.KeyboardModifier.ShiftModifier) and self.rulate_last_clicked_row != -1:
            start = min(self.rulate_last_clicked_row, row)
            end = max(self.rulate_last_clicked_row, row)
            state = item.checkState()
            for i in range(start, end + 1):
                curr = self.rulate_list_widget.item(i)
                ch = curr.data(Qt.ItemDataRole.UserRole)
                if ch and ch.get("downloadable", False):
                    curr.setCheckState(state)

        self.rulate_last_clicked_row = row

    # ══════════════════════════════════════════════════════════════════
    #  v12: RULATE — скачивание выбранных глав
    # ══════════════════════════════════════════════════════════════════

    def _download_rulate_chapters(self):
        """Скачать выбранные главы с Rulate."""
        selected_ids = []
        for i in range(self.rulate_list_widget.count()):
            item = self.rulate_list_widget.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                ch = item.data(Qt.ItemDataRole.UserRole)
                if ch and ch.get("id"):
                    selected_ids.append(ch["id"])

        if not selected_ids:
            return QMessageBox.warning(self, "Ошибка", "Не выбрано ни одной главы для скачивания.")

        answer = QMessageBox.question(
            self,
            "Подтверждение скачивания",
            f"Будет скачано глав: {len(selected_ids)}\n\n"
            f"После скачивания главы появятся в списке ниже для загрузки на RanobeLib.\n\n"
            f"Продолжить?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        rulate_url = self.rulate_url_input.text().strip()
        vol = self.default_vol_input.text().strip() or "1"

        self.btn_download_rulate.setEnabled(False)
        self.btn_fetch_rulate.setEnabled(False)
        self.progress_bar.setValue(0)
        self._open_process_dialog("rulate_download", "Rulate: скачивание выбранных глав")

        self._rulate_dl_worker = RulateDownloadWorker(
            rulate_url=rulate_url,
            default_vol=vol,
            chapter_ids=selected_ids,
        )
        self._rulate_dl_worker.log_signal.connect(
            lambda level, msg: self._process_log("rulate_download", level, msg)
        )
        self._rulate_dl_worker.progress_signal.connect(
            lambda val: self._process_progress("rulate_download", val)
        )
        self._rulate_dl_worker.chapters_ready.connect(self._on_rulate_chapters_downloaded)
        self._rulate_dl_worker.finished_signal.connect(self._on_rulate_download_finished)
        self._rulate_dl_worker.start()

    def _on_rulate_chapters_downloaded(self, chapters: list):
        """Главы скачаны с Rulate — загружаем в основной список."""
        self.chapters_to_upload = chapters
        self._current_file_path = "(Rulate)"
        self._populate_chapter_list()

        if chapters:
            total_chars = sum(c.content_length for c in chapters)
            self.lbl_info.setText(
                f"Rulate: {len(chapters)} глав ({total_chars:,} символов)"
            )
            self._append_log(
                "SUCCESS",
                f"Из Rulate загружено: {len(chapters)} глав, {total_chars:,} символов",
            )
            self.btn_start.setEnabled(True)
        else:
            self.lbl_info.setText("Rulate: глав не получено")

    def _on_rulate_download_finished(self):
        self.btn_download_rulate.setEnabled(True)
        self.btn_fetch_rulate.setEnabled(True)
        self._finish_process_dialog("rulate_download")

    # ══════════════════════════════════════════════════════════════════

    # ── Выбор / загрузка файла ──

    def _select_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите файл",
            "",
            "Books (*.epub *.zip *.txt *.md *.html *.htm)",
        )
        if path:
            self._load_file(path)

    def _load_file(self, file_path: str):
        self.lbl_info.setText(f"Чтение… {os.path.basename(file_path)}")
        QApplication.processEvents()

        try:
            vol = self.default_vol_input.text().strip() or "1"
            ext = file_path.lower()

            if ext.endswith(".epub"):
                chapters = FileParser.parse_epub(file_path, vol, self._append_log)
            elif ext.endswith(".zip"):
                chapters = FileParser.parse_zip_docx(file_path, vol, self._append_log)
            elif ext.endswith((".html", ".htm")):
                chapters = FileParser.parse_html(file_path, vol)
            else:
                chapters = FileParser.parse_txt(file_path, vol)

            self.chapters_to_upload = chapters
            self._current_file_path = file_path  # Feature 2: запомнить путь
            self._populate_chapter_list()

            if chapters:
                total_chars = sum(c.content_length for c in chapters)
                self.lbl_info.setText(
                    f"Найдено глав: {len(chapters)} ({total_chars:,} символов)"
                )
                self._append_log(
                    "INFO",
                    f"Загружен: {os.path.basename(file_path)} — "
                    f"{len(chapters)} глав, {total_chars:,} символов",
                )
                self.btn_start.setEnabled(True)

                # Feature 2: проверка незавершённой загрузки
                self._check_resume(file_path, len(chapters))
            else:
                self.lbl_info.setText("Глав не найдено")

        except Exception as e:
            self.lbl_info.setText("Ошибка чтения файла")
            self._append_log("ERROR", f"Ошибка файла: {e}")

    # ── Feature 2: Авто-возобновление ──

    def _check_resume(self, file_path: str, total_chapters: int):
        """Проверить, есть ли незавершённая загрузка для этого файла."""
        resume_file = self.settings.value("resume_file", "")
        resume_index = self.settings.value("resume_index", -1, type=int)
        resume_url = self.settings.value("resume_url", "")

        if resume_file == file_path and resume_index >= 0 and resume_index < total_chapters - 1:
            answer = QMessageBox.question(
                self,
                "Незавершённая загрузка",
                f"Обнаружена незавершённая загрузка (глава {resume_index + 1} из {total_chapters}).\n"
                f"Продолжить с главы {resume_index + 2}?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.Yes:
                # Снять галочки со всех глав до resume_index включительно
                for i in range(min(resume_index + 1, self.chapters_list_widget.count())):
                    self.chapters_list_widget.item(i).setCheckState(Qt.CheckState.Unchecked)
                # Восстановить URL, если он был сохранён
                if resume_url:
                    self.url_input.setText(resume_url)
                self._append_log(
                    "INFO",
                    f"Возобновление с главы {resume_index + 2} из {total_chapters}",
                )
            else:
                self._clear_resume_state()

    def _save_resume_state(self, chapter_index: int):
        """Сохранить прогресс загрузки."""
        self.settings.setValue("resume_file", self._current_file_path)
        self.settings.setValue("resume_index", chapter_index)
        self.settings.setValue("resume_url", self.url_input.text())

    def _clear_resume_state(self):
        """Очистить состояние незавершённой загрузки."""
        self.settings.remove("resume_file")
        self.settings.remove("resume_index")
        self.settings.remove("resume_url")

    def _on_chapter_done(self, index: int):
        """Слот для сигнала chapter_done_signal: сохраняем прогресс."""
        self._save_resume_state(index)

    def _populate_chapter_list(self):
        self.chapters_list_widget.clear()
        self.last_clicked_row = -1
        self.search_input.clear()  # Feature 3: сбросить фильтр при загрузке нового файла
        has_fractional = False
        for ch in self.chapters_to_upload:
            label = f"{ch}  [{ch.content_length:,} зн.]"
            item = QListWidgetItem(label)
            item.setCheckState(Qt.CheckState.Checked)
            item.setData(Qt.ItemDataRole.UserRole, ch)
            self.chapters_list_widget.addItem(item)
            if ch.number != int(ch.number):
                has_fractional = True
        self.chapters_group.setEnabled(True)

        # Предупреждение: дробные номера глав (напр. 5.1, 16.2)
        # без принудительной нумерации RanobeLib сам назначит номера последовательно
        if (
            has_fractional
            and not self.chk_force_num.isChecked()
            and self._current_upload_mode() != "api"
        ):
            self._append_log(
                "WARNING",
                "⚠ Обнаружены дробные номера глав (например 5.1, 16.2). "
                "Рекомендуется включить «Вписывать номер главы из файла», "
                "иначе RanobeLib назначит номера последовательно и дробная нумерация потеряется.",
            )
            QMessageBox.warning(
                self,
                "Дробные номера глав",
                "Обнаружены дробные номера глав (например 5.1, 16.2).\n\n"
                "Рекомендуется включить опцию «Вписывать номер главы из файла», "
                "иначе RanobeLib назначит номера последовательно "
                "и дробная нумерация потеряется.",
            )

    # ── Управление списком глав ──

    def _select_all(self):
        for i in range(self.chapters_list_widget.count()):
            self.chapters_list_widget.item(i).setCheckState(Qt.CheckState.Checked)

    def _deselect_all(self):
        for i in range(self.chapters_list_widget.count()):
            self.chapters_list_widget.item(i).setCheckState(Qt.CheckState.Unchecked)

    def _invert_selection(self):
        for i in range(self.chapters_list_widget.count()):
            item = self.chapters_list_widget.item(i)
            new_state = (
                Qt.CheckState.Unchecked
                if item.checkState() == Qt.CheckState.Checked
                else Qt.CheckState.Checked
            )
            item.setCheckState(new_state)

    def _on_item_clicked(self, item):
        row = self.chapters_list_widget.row(item)
        mods = QApplication.keyboardModifiers()
        if (mods & Qt.KeyboardModifier.ShiftModifier) and self.last_clicked_row != -1:
            start = min(self.last_clicked_row, row)
            end = max(self.last_clicked_row, row)
            state = item.checkState()
            for i in range(start, end + 1):
                self.chapters_list_widget.item(i).setCheckState(state)
        self.last_clicked_row = row

    def _on_item_dblclick(self, item):
        """Двойной клик — редактирование названия главы."""
        chapter: ChapterData = item.data(Qt.ItemDataRole.UserRole)
        new_title, ok = QInputDialog.getText(
            self,
            "Редактирование названия",
            f"Гл.{format_num(chapter.number)}:",
            text=chapter.title,
        )
        if ok:
            chapter.title = new_title
            label = f"{chapter}  [{chapter.content_length:,} зн.]"
            item.setText(label)

    def _on_context_menu(self, pos):
        item = self.chapters_list_widget.itemAt(pos)
        if not item:
            return
        chapter: ChapterData = item.data(Qt.ItemDataRole.UserRole)

        menu = QMenu(self)
        act_preview = menu.addAction("Предпросмотр")
        act_edit = menu.addAction("Редактировать название")
        menu.addSeparator()
        act_move_up = menu.addAction("Переместить вверх")
        act_move_down = menu.addAction("Переместить вниз")

        action = menu.exec(self.chapters_list_widget.mapToGlobal(pos))

        if action == act_preview:
            dlg = PreviewDialog(chapter, self)
            dlg.exec()
        elif action == act_edit:
            self._on_item_dblclick(item)
        elif action == act_move_up:
            self._move_item(-1)
        elif action == act_move_down:
            self._move_item(1)

    def _move_item(self, direction: int):
        row = self.chapters_list_widget.currentRow()
        if row < 0:
            return
        new_row = row + direction
        if new_row < 0 or new_row >= self.chapters_list_widget.count():
            return

        item = self.chapters_list_widget.takeItem(row)
        self.chapters_list_widget.insertItem(new_row, item)
        self.chapters_list_widget.setCurrentRow(new_row)

        # Синхронизируем внутренний список
        self.chapters_to_upload[row], self.chapters_to_upload[new_row] = (
            self.chapters_to_upload[new_row],
            self.chapters_to_upload[row],
        )

    # ── Запуск / остановка загрузки ──

    def _start_upload(self):
        url = self.url_input.text().strip()
        upload_mode = self._current_upload_mode()
        if not url:
            return QMessageBox.warning(self, "Ошибка", "Введите URL RanobeLib.")

        if upload_mode == "browser" and not validate_url(url):
            answer = QMessageBox.question(
                self,
                "Подозрительный URL",
                f"URL не соответствует шаблону ranobelib.me/ru/book/.../add-chapter.\n\n"
                f"{url}\n\nВсё равно продолжить?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.No:
                return
        elif upload_mode == "api" and ("/ru/book/" not in url or "ranobelib" not in url):
            answer = QMessageBox.question(
                self,
                "Подозрительный URL",
                "Для API-режима нужен URL книги RanobeLib вида "
                "https://ranobelib.me/ru/book/... или .../add-chapter.\n\n"
                f"{url}\n\nВсё равно продолжить?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.No:
                return

        if upload_mode == "api" and self.chk_paid.isChecked():
            return QMessageBox.warning(
                self,
                "API-режим",
                "Платные главы пока поддерживаются только в браузерном режиме.",
            )

        selected = []
        for i in range(self.chapters_list_widget.count()):
            item = self.chapters_list_widget.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                selected.append(item.data(Qt.ItemDataRole.UserRole))

        if not selected:
            return QMessageBox.warning(self, "Ошибка", "Не выбрано ни одной главы.")

        # Подтверждение
        msg = f"Будет загружено глав: {len(selected)}"
        msg += (
            "\nСпособ: API RanobeLib"
            if upload_mode == "api"
            else "\nСпособ: браузерная автоматизация"
        )
        if self.chk_schedule.isChecked():
            start_dt = self.date_edit.dateTime().toPyDateTime()
            end_dt = start_dt + timedelta(
                minutes=self.interval_spin.value() * (len(selected) - 1)
            )
            msg += f"\nОтложка: {start_dt:%d.%m.%Y %H:%M} → {end_dt:%d.%m.%Y %H:%M}"
        if self.chk_paid.isChecked():
            msg += f"\nПлатные: {self.spin_price.value()}₽"
        if self._current_file_path == "(Rulate)":
            msg += "\n\nИсточник: Rulate"
        msg += "\n\nПродолжить?"

        if QMessageBox.question(
            self,
            "Подтверждение",
            msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return

        self._save_settings()
        self._upload_ok = 0
        self._upload_errors = 0

        worker_class = ApiUploadWorker if upload_mode == "api" else UploadWorker
        self.worker = worker_class(
            url,
            selected,
            self.chk_schedule.isChecked(),
            self.date_edit.dateTime().toPyDateTime(),
            self.interval_spin.value(),
            self.chk_paid.isChecked(),
            self.spin_price.value(),
            self.chk_force_num.isChecked(),
        )
        self._open_process_dialog(
            "upload",
            "RanobeLib: API-загрузка"
            if upload_mode == "api"
            else "RanobeLib: процесс загрузки",
            can_stop=True,
            stop_fn=self._stop_upload,
        )
        self.worker.log_signal.connect(
            lambda level, msg: self._process_log("upload", level, msg)
        )
        self.worker.progress_signal.connect(
            lambda val: self._process_progress("upload", val)
        )
        self.worker.stats_signal.connect(self._update_stats)
        self.worker.eta_signal.connect(lambda s: self.lbl_eta.setText(f"ETA: {s}"))
        self.worker.finished_signal.connect(self._on_upload_finished)
        self.worker.chapter_done_signal.connect(self._on_chapter_done)  # Feature 2

        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.btn_file.setEnabled(False)
        self.progress_bar.setValue(0)
        self.lbl_stats.setText("OK: 0  Ошибки: 0  Пропущено: 0")
        self.worker.start()

    def _stop_upload(self):
        if hasattr(self, "worker") and self.worker:
            self.worker.stop()
        self._append_log("WARNING", "Запрошена остановка…")

    def _on_upload_finished(self):
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.btn_file.setEnabled(True)
        self.lbl_eta.setText("ETA: —")
        self._append_log("SUCCESS", "Загрузка завершена.")

        # Feature 2: очистить прогресс при полной загрузке (без ошибок и пропусков)
        if self._upload_errors == 0 and hasattr(self, "worker") and not self.worker._skipped:
            self._clear_resume_state()

        # Feature 4: уведомление о завершении
        was_stopped = hasattr(self, "worker") and not self.worker.is_running and self.worker._skipped > 0
        if was_stopped:
            self._show_notification(
                APP_NAME,
                f"Загрузка остановлена — OK: {self._upload_ok}, Ошибки: {self._upload_errors}",
            )
        else:
            self._show_notification(
                APP_NAME,
                f"Загрузка завершена — OK: {self._upload_ok}, Ошибки: {self._upload_errors}",
            )
        self._finish_process_dialog("upload")

    def _update_stats(self, ok: int, errors: int, skipped: int):
        self._upload_ok = ok
        self._upload_errors = errors
        self.lbl_stats.setText(
            f"OK: {ok}  Ошибки: {errors}  Пропущено: {skipped}"
        )


