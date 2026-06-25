import json
import logging
import os
import re
import urllib.request
from datetime import datetime, timedelta

from PyQt6.QtCore import QDateTime, QObject, QSettings, QThread, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import (
    QAction,
    QDesktopServices,
    QDragEnterEvent,
    QDropEvent,
    QKeySequence,
    QPixmap,
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
    QFormLayout,
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
    QScrollArea,
    QSpinBox,
    QSystemTrayIcon,
    QTabWidget,
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
    RanobeLibCatalogMatchWorker,
    RANOBELIB_GENRES,
    RANOBELIB_TAGS,
    RulateDownloadWorker,
    RulateToRanobeMetadataWorker,
    RulateToRanobeCreateWorker,
    UploadWorker,
)

try:
    from gemini_translator.ui.widgets.key_management_widget import KeyManagementWidget
    from gemini_translator.ui.widgets.model_settings_widget import ModelSettingsWidget
    from gemini_translator.utils.settings import SettingsManager
except Exception:
    KeyManagementWidget = None
    ModelSettingsWidget = None
    SettingsManager = None


def _split_csv_text(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"[,;\n]+", text or "") if part.strip()]


class _CoverPreviewWorker(QThread):
    image_ready = pyqtSignal(bytes, str)
    failed = pyqtSignal(str, str)

    def __init__(self, url: str, referer: str = ""):
        super().__init__()
        self.url = url
        self.referer = referer

    def run(self):
        try:
            request = urllib.request.Request(
                self.url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
                    ),
                    "Referer": self.referer or "https://tl.rulate.ru/",
                },
            )
            with urllib.request.urlopen(request, timeout=25) as response:
                data = response.read(8 * 1024 * 1024)
            if not data:
                raise RuntimeError("пустой ответ")
            self.image_ready.emit(data, self.url)
        except Exception as error:
            self.failed.emit(str(error), self.url)


class _RanobeEventBus(QObject):
    event_posted = pyqtSignal(dict)
    data_changed = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._topic_subscribers = {}
        self._data_store = {}
        self.event_posted.connect(self._dispatch_to_topics)

    def subscribe(self, event_name: str, callback):
        subscribers = self._topic_subscribers.setdefault(event_name, [])
        if callback not in subscribers:
            subscribers.append(callback)

    def unsubscribe(self, event_name: str, callback):
        subscribers = self._topic_subscribers.get(event_name) or []
        if callback in subscribers:
            subscribers.remove(callback)

    def _dispatch_to_topics(self, event: dict):
        event_name = event.get("event") if isinstance(event, dict) else None
        if not event_name:
            return
        for callback in list(self._topic_subscribers.get(event_name, [])):
            try:
                callback(event)
            except Exception:
                pass

    def emit_event(self, event: dict):
        self.event_posted.emit(event)

    def set_data(self, key: str, value):
        self._data_store[key] = value
        self.data_changed.emit(key)

    def pop_data(self, key: str, default=None):
        return self._data_store.pop(key, default)

    def get_data(self, key: str, default=None):
        return self._data_store.get(key, default)

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
        self._rulate_media_metadata: dict = {}
        self._cover_preview_workers: list[_CoverPreviewWorker] = []
        self._ensure_event_bus()
        self.settings_manager = self._resolve_settings_manager()
        self.server_manager = getattr(QApplication.instance(), "server_manager", None)

        self._build_ui()
        self._setup_tray_icon()
        self._restore_settings()

    def _ensure_event_bus(self):
        app = QApplication.instance()
        if app and not hasattr(app, "event_bus"):
            app.event_bus = _RanobeEventBus()

    def _resolve_settings_manager(self):
        app = QApplication.instance()
        if app and hasattr(app, "get_settings_manager"):
            try:
                return app.get_settings_manager()
            except Exception:
                pass
        if SettingsManager is None:
            return None
        try:
            manager = SettingsManager()
            if app:
                app.settings_manager = manager
                app.get_settings_manager = lambda manager=manager: manager
            return manager
        except Exception as error:
            logging.warning("RanobeLib: SettingsManager недоступен: %s", error)
            return None

    # ── Построение интерфейса ──

    def _build_ui(self):
        main_central = QWidget()
        self.setCentralWidget(main_central)
        main_layout = QVBoxLayout(main_central)
        main_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        central = QWidget()
        scroll.setWidget(central)
        main_layout.addWidget(scroll)

        layout = QVBoxLayout(central)
        layout.setSpacing(6)

        # Верхнее меню (кнопки вместо тулбара)
        top_menu_layout = QHBoxLayout()

        top_menu_layout.addWidget(QLabel("Тема:"))
        self.theme_mode_combo = QComboBox()
        self.theme_mode_combo.addItem("Авто (как в системе)", "auto")
        self.theme_mode_combo.addItem("Светлая", "light")
        self.theme_mode_combo.addItem("Тёмная", "dark")
        self.theme_mode_combo.addItem("Своя", "custom")
        self.theme_mode_combo.currentIndexChanged.connect(
            lambda _i: self._on_theme_mode_changed(self.theme_mode_combo.currentData())
        )
        top_menu_layout.addWidget(self.theme_mode_combo)

        btn_export_log = QPushButton("📋 Экспорт лога")
        btn_export_log.clicked.connect(self._export_log)
        top_menu_layout.addWidget(btn_export_log)

        btn_open_log_dir = QPushButton("📂 Папка логов")
        btn_open_log_dir.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(LOG_DIR.resolve())))
        )
        top_menu_layout.addWidget(btn_open_log_dir)

        top_menu_layout.addStretch()

        # ── Feature 1: Профили ──
        top_menu_layout.addWidget(QLabel(" Профиль: "))
        self.profile_combo = QComboBox()
        self.profile_combo.setMinimumWidth(150)
        self.profile_combo.addItem("(Текущий)")
        self.profile_combo.currentIndexChanged.connect(self._on_profile_selected)
        top_menu_layout.addWidget(self.profile_combo)

        self.btn_save_profile = QPushButton("Сохранить профиль")
        self.btn_save_profile.clicked.connect(self._save_profile)
        top_menu_layout.addWidget(self.btn_save_profile)

        self.btn_delete_profile = QPushButton("Удалить профиль")
        self.btn_delete_profile.clicked.connect(self._delete_profile)
        top_menu_layout.addWidget(self.btn_delete_profile)

        layout.addLayout(top_menu_layout)

        # StatusBar
        self.statusBar().showMessage("Готов")
        self.lbl_stats = QLabel("OK: 0  Ошибки: 0  Пропущено: 0")
        self.statusBar().addPermanentWidget(self.lbl_stats)

        self.main_tabs = QTabWidget()
        layout.addWidget(self.main_tabs, 1)
        self.main_tabs.addTab(self._build_rulate_media_tab(), "Rulate → RanobeLib")
        self._connect_rulate_media_ai_widgets()

        upload_tab = QWidget()
        upload_layout = QVBoxLayout(upload_tab)
        upload_layout.setSpacing(6)
        layout = upload_layout

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

        self.btn_login_rulate = QPushButton("Войти в Rulate (главы)")
        self.btn_login_rulate.setToolTip(
            "Авторизация нужна только для скачивания платных глав.\n"
            "Бесплатные главы скачиваются без входа в аккаунт.\n"
            "Создание карточки RanobeLib использует куки из Qidian/Fanqie → Rulate."
        )
        self.btn_login_rulate.clicked.connect(self._start_login_rulate)
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
        rulate_opts_row.addWidget(self.btn_fetch_rulate)

        self.btn_download_rulate = QPushButton("Скачать выбранные главы")
        self.btn_download_rulate.clicked.connect(self._download_rulate_chapters)
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
        self.btn_start.setMinimumHeight(40)
        self.btn_stop = QPushButton("СТОП")
        self.btn_stop.clicked.connect(self._stop_upload)
        self.btn_stop.setEnabled(False)
        self.btn_stop.setMinimumHeight(40)
        btn_row.addWidget(self.btn_start)
        btn_row.addWidget(self.btn_stop)
        layout.addLayout(btn_row)

        self.main_tabs.addTab(upload_tab, "Загрузка глав")
        self.main_tabs.setCurrentIndex(0)

        # Шорткаты
        QShortcut(QKeySequence("Ctrl+A"), self).activated.connect(self._select_all)
        QShortcut(QKeySequence("Ctrl+O"), self).activated.connect(self._select_file)

        # Применить начальную тему
        self._apply_theme()
        self._append_log(
            "INFO",
            f"Готов. v{APP_VERSION} — .epub, .zip(docx), .txt, .md, .html + Rulate + API",
        )

    def _build_rulate_media_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        source_group = QGroupBox("Источник")
        source_layout = QVBoxLayout(source_group)
        url_row = QHBoxLayout()
        url_row.addWidget(QLabel("URL Rulate:"))
        self.media_rulate_url_input = QLineEdit()
        self.media_rulate_url_input.setPlaceholderText("https://tl.rulate.ru/book/204281/edit/info")
        url_row.addWidget(self.media_rulate_url_input, 1)
        self.btn_media_copy_url = QPushButton("Из вкладки глав")
        self.btn_media_copy_url.clicked.connect(self._copy_rulate_url_to_media)
        url_row.addWidget(self.btn_media_copy_url)
        source_layout.addLayout(url_row)

        action_row = QHBoxLayout()
        self.btn_fetch_rulate_media = QPushButton("Получить данные Rulate")
        self.btn_fetch_rulate_media.clicked.connect(self._fetch_rulate_media_metadata)
        action_row.addWidget(self.btn_fetch_rulate_media)
        self.btn_match_ranobelib_catalog = QPushButton("AI подобрать жанры/теги")
        self.btn_match_ranobelib_catalog.clicked.connect(self._match_ranobelib_catalog_ai)
        action_row.addWidget(self.btn_match_ranobelib_catalog)
        self.btn_create_ranobelib_media = QPushButton("Открыть и заполнить RanobeLib")
        self.btn_create_ranobelib_media.clicked.connect(self._create_ranobelib_media_from_rulate)
        action_row.addWidget(self.btn_create_ranobelib_media)
        action_row.addStretch()
        source_layout.addLayout(action_row)
        layout.addWidget(source_group)

        form_group = QGroupBox("Карточка")
        form = QFormLayout(form_group)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.media_title_ru_edit = QLineEdit()
        self.media_original_title_edit = QLineEdit()
        self.media_alt_hieroglyph_edit = QLineEdit()
        self.media_title_en_edit = QLineEdit()
        self.media_alt_names_edit = QLineEdit()
        self.media_author_edit = QLineEdit()
        author_widget = QWidget()
        author_layout = QHBoxLayout(author_widget)
        author_layout.setContentsMargins(0, 0, 0, 0)
        author_layout.addWidget(self.media_author_edit, 1)
        self.media_create_author_chk = QCheckBox("создать если не найден")
        self.media_create_author_chk.setChecked(True)
        author_layout.addWidget(self.media_create_author_chk)

        self.media_cover_url_edit = QLineEdit()
        self.media_cover_url_edit.editingFinished.connect(self._refresh_media_cover_preview)
        self.btn_media_cover_preview = QPushButton("Обновить превью")
        self.btn_media_cover_preview.clicked.connect(self._refresh_media_cover_preview)
        cover_url_widget = QWidget()
        cover_url_layout = QHBoxLayout(cover_url_widget)
        cover_url_layout.setContentsMargins(0, 0, 0, 0)
        cover_url_layout.addWidget(self.media_cover_url_edit, 1)
        cover_url_layout.addWidget(self.btn_media_cover_preview)

        self.media_cover_preview_label = QLabel("Нет обложки")
        self.media_cover_preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.media_cover_preview_label.setFixedSize(150, 220)
        self.media_cover_preview_label.setStyleSheet(
            "QLabel { border: 1px solid #555; background: #202020; color: #aaa; }"
        )

        self.media_year_edit = QLineEdit()
        self.media_year_edit.setText("2026")
        self.media_year_edit.setPlaceholderText("2026")

        self.media_type_combo = QComboBox()
        self.media_type_combo.addItem("Китай", "12")
        self.media_type_combo.addItem("Япония", "10")
        self.media_type_combo.addItem("Корея", "11")
        self.media_type_combo.addItem("Английский", "13")
        self.media_type_combo.addItem("Авторский", "14")
        self.media_type_combo.addItem("Фанфик", "15")

        self.media_status_combo = QComboBox()
        self.media_status_combo.addItem("Онгоинг", "1")
        self.media_status_combo.addItem("Завершён", "2")
        self.media_status_combo.addItem("Анонс", "3")
        self.media_status_combo.addItem("Приостановлен", "4")
        self.media_status_combo.addItem("Выпуск прекращён", "5")

        self.media_age_combo = QComboBox()
        self.media_age_combo.addItem("16+", "3")
        self.media_age_combo.addItem("6+", "1")
        self.media_age_combo.addItem("12+", "2")
        self.media_age_combo.addItem("18+", "4")

        self.media_translation_status_combo = QComboBox()
        self.media_translation_status_combo.addItem("Продолжается", "1")
        self.media_translation_status_combo.addItem("Завершён", "2")
        self.media_translation_status_combo.addItem("Заморожен", "3")
        self.media_translation_status_combo.addItem("Заброшен", "4")

        self.media_chapter_upload_combo = QComboBox()
        self.media_chapter_upload_combo.addItem("Создатель и переводчики", "2")
        self.media_chapter_upload_combo.addItem("Все", "0")

        self.media_rulate_genres_edit = QTextEdit()
        self.media_rulate_genres_edit.setAcceptRichText(False)
        self.media_rulate_genres_edit.setReadOnly(True)
        self.media_rulate_genres_edit.setMaximumHeight(62)

        self.media_rulate_tags_edit = QTextEdit()
        self.media_rulate_tags_edit.setAcceptRichText(False)
        self.media_rulate_tags_edit.setReadOnly(True)
        self.media_rulate_tags_edit.setMaximumHeight(70)

        self.media_genres_edit = QTextEdit()
        self.media_genres_edit.setAcceptRichText(False)
        self.media_genres_edit.setMaximumHeight(70)
        self.media_genres_edit.setPlaceholderText(", ".join(RANOBELIB_GENRES[:8]) + "...")

        self.media_tags_edit = QTextEdit()
        self.media_tags_edit.setAcceptRichText(False)
        self.media_tags_edit.setMaximumHeight(90)
        self.media_tags_edit.setPlaceholderText(", ".join(RANOBELIB_TAGS[:8]) + "...")

        self.media_description_edit = QTextEdit()
        self.media_description_edit.setAcceptRichText(False)
        self.media_description_edit.setMinimumHeight(110)

        form.addRow("Название RU:", self.media_title_ru_edit)
        form.addRow("Оригинальное название:", self.media_original_title_edit)
        form.addRow("Альтернативное (иероглифы):", self.media_alt_hieroglyph_edit)
        form.addRow("Название EN:", self.media_title_en_edit)
        form.addRow("Альтернативные:", self.media_alt_names_edit)
        form.addRow("Автор:", author_widget)
        form.addRow("Обложка URL:", cover_url_widget)
        form.addRow("Превью обложки:", self.media_cover_preview_label)
        form.addRow("Год релиза:", self.media_year_edit)
        form.addRow("Тип:", self.media_type_combo)
        form.addRow("Статус тайтла:", self.media_status_combo)
        form.addRow("Возраст:", self.media_age_combo)
        form.addRow("Статус перевода:", self.media_translation_status_combo)
        form.addRow("Загрузка глав:", self.media_chapter_upload_combo)
        form.addRow("Жанры Rulate:", self.media_rulate_genres_edit)
        form.addRow("Теги Rulate:", self.media_rulate_tags_edit)
        form.addRow("Жанры RanobeLib:", self.media_genres_edit)
        form.addRow("Теги RanobeLib:", self.media_tags_edit)
        form.addRow("Описание:", self.media_description_edit)
        layout.addWidget(form_group)

        ai_group = QGroupBox("AI")
        ai_layout = QVBoxLayout(ai_group)
        if KeyManagementWidget and ModelSettingsWidget and self.settings_manager:
            self.media_key_widget = KeyManagementWidget(
                self.settings_manager,
                self,
                server_manager=self.server_manager,
            )
            self.media_model_settings_widget = ModelSettingsWidget(
                self,
                settings_manager=self.settings_manager,
                server_manager=self.server_manager,
            )
            self.media_model_settings_widget.set_cjk_options_visible(False)
            self.media_model_settings_widget.set_glossary_options_visible(False)
            self.media_model_settings_widget.set_misc_options_visible(False)
            ai_layout.addWidget(self.media_key_widget)
            ai_layout.addWidget(self.media_model_settings_widget)
        else:
            self.media_key_widget = None
            self.media_model_settings_widget = None
            ai_layout.addWidget(QLabel("AI-настройки недоступны в этой сборке."))
            self.btn_match_ranobelib_catalog.setEnabled(False)
        layout.addWidget(ai_group)

        hint = QLabel(
            "Форма RanobeLib заполняется в браузере. Финальная кнопка «Создать» не нажимается автоматически."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        return tab

    def _connect_rulate_media_ai_widgets(self):
        if not self.media_key_widget or not self.media_model_settings_widget:
            return
        provider_id = self.media_key_widget.get_selected_provider()
        self.media_model_settings_widget.set_available_models(provider_id)
        self.media_key_widget.provider_combo.currentIndexChanged.connect(
            self._on_media_provider_changed
        )
        self.media_model_settings_widget.model_combo.currentIndexChanged.connect(
            self._on_media_model_changed
        )
        self._on_media_model_changed(self.media_model_settings_widget.model_combo.currentIndex())

    def _on_media_provider_changed(self, _index: int):
        if not self.media_key_widget or not self.media_model_settings_widget:
            return
        provider_id = self.media_key_widget.get_selected_provider()
        self.media_model_settings_widget.set_available_models(provider_id)
        self._on_media_model_changed(self.media_model_settings_widget.model_combo.currentIndex())

    def _on_media_model_changed(self, index: int):
        if not self.media_key_widget or not self.media_model_settings_widget or index < 0:
            return
        model_id = self.media_model_settings_widget.model_combo.itemData(index)
        if model_id:
            self.media_key_widget.set_current_model(model_id)

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
        self.settings.setValue("media_rulate_url", self.media_rulate_url_input.text())
        self.settings.setValue("media_title_ru", self.media_title_ru_edit.text())
        self.settings.setValue("media_original_title", self.media_original_title_edit.text())
        self.settings.setValue("media_title_en", self.media_title_en_edit.text())
        self.settings.setValue("media_alt_names", self.media_alt_names_edit.text())
        self.settings.setValue("media_alt_hieroglyph", self.media_alt_hieroglyph_edit.text())
        self.settings.setValue("media_author", self.media_author_edit.text())
        self.settings.setValue("media_cover_url", self.media_cover_url_edit.text())
        self.settings.setValue("media_year", self.media_year_edit.text())
        self.settings.setValue("media_description", self.media_description_edit.toPlainText())
        self.settings.setValue("media_rulate_genres", self.media_rulate_genres_edit.toPlainText())
        self.settings.setValue("media_rulate_tags", self.media_rulate_tags_edit.toPlainText())
        self.settings.setValue("media_genres", self.media_genres_edit.toPlainText())
        self.settings.setValue("media_tags", self.media_tags_edit.toPlainText())
        self.settings.setValue("media_type", self.media_type_combo.currentData())
        self.settings.setValue("media_status", self.media_status_combo.currentData())
        self.settings.setValue("media_age", self.media_age_combo.currentData())
        self.settings.setValue(
            "media_translation_status",
            self.media_translation_status_combo.currentData(),
        )
        self.settings.setValue("media_chapter_upload", self.media_chapter_upload_combo.currentData())
        self.settings.setValue("media_create_author", self.media_create_author_chk.isChecked())
        self._save_rulate_media_state()
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
        media_rulate_url = self.settings.value("media_rulate_url", "")
        cached_media = self._load_rulate_media_state()
        self._rulate_media_metadata = dict(cached_media)
        if media_rulate_url:
            self.media_rulate_url_input.setText(media_rulate_url)
        elif rulate_url:
            self.media_rulate_url_input.setText(rulate_url)
        elif cached_media.get("rulate_edit_url") or cached_media.get("rulate_url"):
            self.media_rulate_url_input.setText(cached_media.get("rulate_edit_url") or cached_media.get("rulate_url"))
        self.media_title_ru_edit.setText(self._settings_text("media_title_ru", cached_media.get("title_ru", "")))
        self.media_original_title_edit.setText(
            self._settings_text("media_original_title", cached_media.get("original_title", ""))
        )
        self.media_title_en_edit.setText(self._settings_text("media_title_en", cached_media.get("title_en", "")))
        self.media_alt_names_edit.setText(self._settings_text("media_alt_names", cached_media.get("alt_names", "")))
        self.media_alt_hieroglyph_edit.setText(
            self._settings_text("media_alt_hieroglyph", cached_media.get("alt_hieroglyph_title", ""))
        )
        self.media_author_edit.setText(self._settings_text("media_author", cached_media.get("author", "")))
        self.media_cover_url_edit.setText(self._settings_text("media_cover_url", cached_media.get("cover_url", "")))
        self.media_year_edit.setText(str(self.settings.value("media_year", cached_media.get("year") or "2026") or "2026"))
        self.media_description_edit.setPlainText(
            self._settings_text("media_description", cached_media.get("description", ""))
        )
        self.media_rulate_genres_edit.setPlainText(
            self.settings.value("media_rulate_genres", ", ".join(cached_media.get("rulate_genres") or []))
        )
        self.media_rulate_tags_edit.setPlainText(
            self.settings.value("media_rulate_tags", ", ".join(cached_media.get("rulate_tags") or []))
        )
        self.media_genres_edit.setPlainText(
            self.settings.value("media_genres", ", ".join(cached_media.get("genres") or []))
        )
        self.media_tags_edit.setPlainText(
            self.settings.value("media_tags", ", ".join(cached_media.get("tags") or []))
        )
        self._set_combo_data(self.media_type_combo, self.settings.value("media_type", "12"))
        self._set_combo_data(self.media_status_combo, self.settings.value("media_status", "1"))
        self._set_combo_data(self.media_age_combo, self.settings.value("media_age", "3"))
        self._set_combo_data(
            self.media_translation_status_combo,
            self.settings.value("media_translation_status", "1"),
        )
        self._set_combo_data(
            self.media_chapter_upload_combo,
            self._default_media_chapter_upload_value(),
        )
        self.media_create_author_chk.setChecked(
            self.settings.value("media_create_author", True, type=bool)
        )
        self.settings.endGroup()
        self._on_upload_mode_changed(self.upload_mode_combo.currentIndex())
        if self.media_cover_url_edit.text().strip():
            self._refresh_media_cover_preview()

        self._append_log("INFO", f"Профиль «{name}» загружен.")

    # ── Feature 3: Поиск / фильтрация глав ──

    def _filter_chapters(self, text: str):
        """Скрыть/показать элементы списка глав по поисковому запросу."""
        query = text.strip().lower()
        search_role = Qt.ItemDataRole.UserRole + 1
        self.chapters_list_widget.setUpdatesEnabled(False)
        try:
            for i in range(self.chapters_list_widget.count()):
                item = self.chapters_list_widget.item(i)
                if not query:
                    item.setHidden(False)
                    continue
                searchable = item.data(search_role)
                if searchable is None:
                    chapter: ChapterData = item.data(Qt.ItemDataRole.UserRole)
                    searchable = f"{chapter.title} {format_num(chapter.number)} {chapter.volume}".lower()
                    item.setData(search_role, searchable)
                item.setHidden(query not in searchable)
        finally:
            self.chapters_list_widget.setUpdatesEnabled(True)

    # ── Тема ──

    def _on_theme_mode_changed(self, mode: str):
        if mode == "auto":
            from PyQt6.QtWidgets import QApplication
            try:
                from gemini_translator.ui import theme_manager
                is_dark = theme_manager.system_is_dark(QApplication.instance())
            except ImportError:
                is_dark = False
        elif mode in ("dark", "custom"):
            is_dark = True
        else:
            is_dark = False
            
        self._is_dark = is_dark
        self._apply_theme()
        self.settings.setValue("theme_mode", mode)

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
        theme_mode = self.settings.value("theme_mode", "light")
        if isinstance(theme_mode, bool) or theme_mode in ("true", "false", True, False):
            is_dark_old = self.settings.value("dark_theme", False, type=bool)
            theme_mode = "dark" if is_dark_old else "light"
        
        idx = self.theme_mode_combo.findData(theme_mode)
        if idx >= 0:
            self.theme_mode_combo.setCurrentIndex(idx)
        else:
            self.theme_mode_combo.setCurrentIndex(self.theme_mode_combo.findData("light"))
            
        self._on_theme_mode_changed(theme_mode)
        rulate_url = self.settings.value("rulate_url", "")
        if rulate_url:
            self.rulate_url_input.setText(rulate_url)
        media_rulate_url = self.settings.value("media_rulate_url", "")
        cached_media = self._load_rulate_media_state()
        self._rulate_media_metadata = dict(cached_media)
        if media_rulate_url:
            self.media_rulate_url_input.setText(media_rulate_url)
        elif rulate_url:
            self.media_rulate_url_input.setText(rulate_url)
        elif cached_media.get("rulate_edit_url") or cached_media.get("rulate_url"):
            self.media_rulate_url_input.setText(cached_media.get("rulate_edit_url") or cached_media.get("rulate_url"))
        self.media_title_ru_edit.setText(self._settings_text("media_title_ru", cached_media.get("title_ru", "")))
        self.media_original_title_edit.setText(
            self._settings_text("media_original_title", cached_media.get("original_title", ""))
        )
        self.media_title_en_edit.setText(self._settings_text("media_title_en", cached_media.get("title_en", "")))
        self.media_alt_names_edit.setText(self._settings_text("media_alt_names", cached_media.get("alt_names", "")))
        self.media_alt_hieroglyph_edit.setText(
            self._settings_text("media_alt_hieroglyph", cached_media.get("alt_hieroglyph_title", ""))
        )
        self.media_author_edit.setText(self._settings_text("media_author", cached_media.get("author", "")))
        self.media_cover_url_edit.setText(self._settings_text("media_cover_url", cached_media.get("cover_url", "")))
        self.media_year_edit.setText(str(self.settings.value("media_year", cached_media.get("year") or "2026") or "2026"))
        self.media_description_edit.setPlainText(
            self._settings_text("media_description", cached_media.get("description", ""))
        )
        self.media_rulate_genres_edit.setPlainText(
            self.settings.value("media_rulate_genres", ", ".join(cached_media.get("rulate_genres") or []))
        )
        self.media_rulate_tags_edit.setPlainText(
            self.settings.value("media_rulate_tags", ", ".join(cached_media.get("rulate_tags") or []))
        )
        self.media_genres_edit.setPlainText(
            self.settings.value("media_genres", ", ".join(cached_media.get("genres") or []))
        )
        self.media_tags_edit.setPlainText(
            self.settings.value("media_tags", ", ".join(cached_media.get("tags") or []))
        )
        self._set_combo_data(self.media_type_combo, self.settings.value("media_type", "12"))
        self._set_combo_data(self.media_status_combo, self.settings.value("media_status", "1"))
        self._set_combo_data(self.media_age_combo, self.settings.value("media_age", "3"))
        self._set_combo_data(
            self.media_translation_status_combo,
            self.settings.value("media_translation_status", "1"),
        )
        self._set_combo_data(
            self.media_chapter_upload_combo,
            self._default_media_chapter_upload_value(),
        )
        self.media_create_author_chk.setChecked(
            self.settings.value("media_create_author", True, type=bool)
        )
        self.chk_skip_uploaded.setChecked(
            self.settings.value("skip_uploaded", True, type=bool)
        )
        # Feature 1: загрузить список профилей
        self._load_profile_list()
        self._on_upload_mode_changed(self.upload_mode_combo.currentIndex())
        if self.media_cover_url_edit.text().strip():
            self._refresh_media_cover_preview()

    def _save_settings(self):
        self.settings.setValue("url", self.url_input.text())
        self.settings.setValue("upload_mode", self._current_upload_mode())
        self.settings.setValue("default_vol", self.default_vol_input.text())
        self.settings.setValue("force_num", self.chk_force_num.isChecked())
        self.settings.setValue("schedule", self.chk_schedule.isChecked())
        self.settings.setValue("interval", self.interval_spin.value())
        self.settings.setValue("paid", self.chk_paid.isChecked())
        self.settings.setValue("price", self.spin_price.value())
        self.settings.setValue("theme_mode", self.theme_mode_combo.currentData())
        self.settings.setValue("rulate_url", self.rulate_url_input.text())
        self.settings.setValue("media_rulate_url", self.media_rulate_url_input.text())
        self.settings.setValue("media_title_ru", self.media_title_ru_edit.text())
        self.settings.setValue("media_original_title", self.media_original_title_edit.text())
        self.settings.setValue("media_title_en", self.media_title_en_edit.text())
        self.settings.setValue("media_alt_names", self.media_alt_names_edit.text())
        self.settings.setValue("media_alt_hieroglyph", self.media_alt_hieroglyph_edit.text())
        self.settings.setValue("media_author", self.media_author_edit.text())
        self.settings.setValue("media_cover_url", self.media_cover_url_edit.text())
        self.settings.setValue("media_year", self.media_year_edit.text())
        self.settings.setValue("media_description", self.media_description_edit.toPlainText())
        self.settings.setValue("media_rulate_genres", self.media_rulate_genres_edit.toPlainText())
        self.settings.setValue("media_rulate_tags", self.media_rulate_tags_edit.toPlainText())
        self.settings.setValue("media_genres", self.media_genres_edit.toPlainText())
        self.settings.setValue("media_tags", self.media_tags_edit.toPlainText())
        self.settings.setValue("media_type", self.media_type_combo.currentData())
        self.settings.setValue("media_status", self.media_status_combo.currentData())
        self.settings.setValue("media_age", self.media_age_combo.currentData())
        self.settings.setValue(
            "media_translation_status",
            self.media_translation_status_combo.currentData(),
        )
        self.settings.setValue("media_chapter_upload", self.media_chapter_upload_combo.currentData())
        self.settings.setValue("media_create_author", self.media_create_author_chk.isChecked())
        self.settings.setValue("skip_uploaded", self.chk_skip_uploaded.isChecked())
        self._save_rulate_media_state()

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

    def _media_rulate_url(self) -> str:
        return self.media_rulate_url_input.text().strip() or self.rulate_url_input.text().strip()

    def _copy_rulate_url_to_media(self):
        self.media_rulate_url_input.setText(self.rulate_url_input.text().strip())

    def _set_combo_data(self, combo: QComboBox, value: str):
        index = combo.findData(str(value))
        if index >= 0:
            combo.setCurrentIndex(index)

    def _settings_text(self, key: str, fallback: str = "") -> str:
        value = self.settings.value(key, fallback)
        return str(value or "")

    def _load_rulate_media_state(self) -> dict:
        raw = self.settings.value("media_last_metadata_json", "")
        if not raw:
            return {}
        try:
            payload = json.loads(str(raw))
        except Exception as error:
            logging.warning("RanobeLib: failed to read cached media metadata: %s", error)
            return {}
        return payload if isinstance(payload, dict) else {}

    def _save_rulate_media_state(self, sync: bool = False):
        try:
            metadata = self._collect_rulate_media_options()
            metadata["rulate_url"] = self._media_rulate_url()
            metadata["rulate_edit_url"] = metadata.get("rulate_edit_url") or self._media_rulate_url()
            metadata["source_url"] = metadata.get("source_url") or metadata["rulate_url"]
            self._rulate_media_metadata = dict(metadata)
            self.settings.setValue(
                "media_last_metadata_json",
                json.dumps(metadata, ensure_ascii=False),
            )
            if sync:
                self.settings.sync()
        except Exception as error:
            logging.warning("RanobeLib: failed to cache media metadata: %s", error)

    def _set_media_cover_preview_text(self, text: str):
        self.media_cover_preview_label.clear()
        self.media_cover_preview_label.setText(text)

    def _refresh_media_cover_preview(self):
        url = self.media_cover_url_edit.text().strip()
        if not url:
            self._set_media_cover_preview_text("Нет обложки")
            return
        self._set_media_cover_preview_text("Загрузка...")
        worker = _CoverPreviewWorker(url, self._media_rulate_url())
        self._cover_preview_workers.append(worker)
        worker.image_ready.connect(self._on_media_cover_preview_ready)
        worker.failed.connect(self._on_media_cover_preview_failed)
        worker.finished.connect(lambda worker=worker: self._forget_cover_preview_worker(worker))
        worker.start()

    def _forget_cover_preview_worker(self, worker: _CoverPreviewWorker):
        if worker in self._cover_preview_workers:
            self._cover_preview_workers.remove(worker)

    def _on_media_cover_preview_ready(self, data: bytes, url: str):
        if url != self.media_cover_url_edit.text().strip():
            return
        pixmap = QPixmap()
        if not pixmap.loadFromData(data):
            self._set_media_cover_preview_text("Не удалось\nпрочитать")
            return
        scaled = pixmap.scaled(
            self.media_cover_preview_label.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.media_cover_preview_label.setText("")
        self.media_cover_preview_label.setPixmap(scaled)
        self.media_cover_preview_label.setToolTip(url)

    def _on_media_cover_preview_failed(self, error: str, url: str):
        if url != self.media_cover_url_edit.text().strip():
            return
        self._set_media_cover_preview_text("Нет превью")
        self.media_cover_preview_label.setToolTip(error)

    def _default_media_chapter_upload_value(self) -> str:
        value = self.settings.value("media_chapter_upload", None)
        if value in (None, ""):
            return "2"
        migrated = self.settings.value("media_chapter_upload_default_v2", False, type=bool)
        if str(value) == "0" and not migrated:
            self.settings.setValue("media_chapter_upload_default_v2", True)
            return "2"
        return str(value)

    def _fetch_rulate_media_metadata(self):
        rulate_url = self._media_rulate_url()
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

        self.progress_bar.setValue(0)
        self._open_process_dialog("rulate_media_fetch", "Rulate: данные карточки")
        self.btn_fetch_rulate_media.setEnabled(False)

        self._rulate_media_fetch_worker = RulateToRanobeMetadataWorker(rulate_url)
        self._rulate_media_fetch_worker.log_signal.connect(
            lambda level, msg: self._process_log("rulate_media_fetch", level, msg)
        )
        self._rulate_media_fetch_worker.metadata_ready.connect(self._apply_rulate_media_metadata)
        self._rulate_media_fetch_worker.finished_signal.connect(self._on_rulate_media_fetch_finished)
        self._rulate_media_fetch_worker.start()

    def _on_rulate_media_fetch_finished(self):
        self.btn_fetch_rulate_media.setEnabled(True)
        self._finish_process_dialog("rulate_media_fetch")

    def _apply_rulate_media_metadata(self, metadata: dict):
        self._rulate_media_metadata = dict(metadata or {})
        self.media_rulate_url_input.setText(
            metadata.get("rulate_edit_url") or metadata.get("source_url", self._media_rulate_url())
        )
        self.media_title_ru_edit.setText(metadata.get("title_ru", ""))
        self.media_original_title_edit.setText(metadata.get("original_title", ""))
        self.media_alt_hieroglyph_edit.setText(metadata.get("alt_hieroglyph_title", ""))
        self.media_title_en_edit.setText(metadata.get("title_en", ""))
        self.media_alt_names_edit.setText(metadata.get("alt_names", ""))
        self.media_author_edit.setText(metadata.get("author", ""))
        self.media_cover_url_edit.setText(metadata.get("cover_url", ""))
        self._refresh_media_cover_preview()
        self.media_year_edit.setText(metadata.get("year") or "2026")
        self.media_description_edit.setPlainText(metadata.get("description", ""))
        self._set_combo_data(self.media_status_combo, metadata.get("status_value", "1"))
        self.media_rulate_genres_edit.setPlainText(", ".join(metadata.get("genres") or []))
        self.media_rulate_tags_edit.setPlainText(", ".join(metadata.get("tags") or []))
        self.media_genres_edit.clear()
        self.media_tags_edit.clear()
        self._save_rulate_media_state(sync=True)
        self._process_log("rulate_media_fetch", "SUCCESS", "Данные Rulate перенесены в форму карточки.")

    def _collect_rulate_media_options(self) -> dict:
        options = dict(self._rulate_media_metadata)
        alt_names = self.media_alt_names_edit.text().strip()
        alt_hieroglyph = self.media_alt_hieroglyph_edit.text().strip()
        alt_values = []
        for value in (alt_names, alt_hieroglyph):
            if value and value not in alt_values:
                alt_values.append(value)
        options.update(
            {
                "title_ru": self.media_title_ru_edit.text().strip(),
                "original_title": self.media_original_title_edit.text().strip(),
                "alt_hieroglyph_title": alt_hieroglyph,
                "title_en": self.media_title_en_edit.text().strip(),
                "alt_names": " / ".join(alt_values),
                "author": self.media_author_edit.text().strip(),
                "cover_url": self.media_cover_url_edit.text().strip(),
                "year": self.media_year_edit.text().strip(),
                "description": self.media_description_edit.toPlainText().strip(),
                "rulate_genres": _split_csv_text(self.media_rulate_genres_edit.toPlainText()),
                "rulate_tags": _split_csv_text(self.media_rulate_tags_edit.toPlainText()),
                "type_value": self.media_type_combo.currentData() or "12",
                "status_value": self.media_status_combo.currentData() or "1",
                "age_value": self.media_age_combo.currentData() or "3",
                "translation_status_value": self.media_translation_status_combo.currentData() or "1",
                "chapter_upload_value": self.media_chapter_upload_combo.currentData() or "2",
                "genres": _split_csv_text(self.media_genres_edit.toPlainText()),
                "tags": _split_csv_text(self.media_tags_edit.toPlainText()),
                "create_author": self.media_create_author_chk.isChecked(),
            }
        )
        return options

    def _match_ranobelib_catalog_ai(self):
        metadata = self._collect_rulate_media_options()
        if not metadata.get("title_ru") and not metadata.get("description"):
            QMessageBox.warning(self, "AI", "Сначала получите данные Rulate или заполните название/описание.")
            return
        if not self.media_key_widget or not self.media_model_settings_widget:
            QMessageBox.warning(self, "AI", "AI-настройки недоступны.")
            return

        provider_id = self.media_key_widget.get_selected_provider()
        active_keys = self.media_key_widget.get_active_keys()
        model_settings = self.media_model_settings_widget.get_settings()
        self._open_process_dialog("rulate_media_ai", "AI: RanobeLib жанры и теги")
        self.btn_match_ranobelib_catalog.setEnabled(False)
        self._rulate_media_ai_worker = RanobeLibCatalogMatchWorker(
            metadata,
            provider_id,
            model_settings,
            active_keys,
            self.settings_manager,
        )
        self._rulate_media_ai_worker.log_signal.connect(
            lambda level, msg: self._process_log("rulate_media_ai", level, msg)
        )
        self._rulate_media_ai_worker.catalog_ready.connect(self._apply_ranobelib_catalog)
        self._rulate_media_ai_worker.finished_signal.connect(self._on_rulate_media_ai_finished)
        self._rulate_media_ai_worker.start()

    def _apply_ranobelib_catalog(self, catalog: dict):
        if catalog.get("genres"):
            self.media_genres_edit.setPlainText(", ".join(catalog["genres"]))
        if catalog.get("tags"):
            self.media_tags_edit.setPlainText(", ".join(catalog["tags"]))
        if catalog.get("year") and not self.media_year_edit.text().strip():
            self.media_year_edit.setText(catalog["year"])
        self._set_combo_data(self.media_age_combo, catalog.get("age_value", "3"))
        self._set_combo_data(self.media_status_combo, catalog.get("status_value", "1"))
        self._set_combo_data(
            self.media_translation_status_combo,
            catalog.get("translation_status_value", "1"),
        )
        self._process_log("rulate_media_ai", "SUCCESS", "AI-результат применён к форме карточки.")

        self._save_rulate_media_state(sync=True)

    def _on_rulate_media_ai_finished(self):
        self.btn_match_ranobelib_catalog.setEnabled(True)
        self._finish_process_dialog("rulate_media_ai")

    def _create_ranobelib_media_from_rulate(self):
        """Создать карточку RanobeLib из страницы книги Rulate."""
        rulate_url = self._media_rulate_url()
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

        options = self._collect_rulate_media_options()
        missing = []
        if not options.get("title_ru"):
            missing.append("название RU")
        if not options.get("description"):
            missing.append("описание")
        if not options.get("author"):
            missing.append("автор")
        if len(options.get("genres") or []) < 3:
            missing.append("минимум 3 жанра")
        if len(options.get("tags") or []) < 3:
            missing.append("минимум 3 тега")
        if missing:
            answer = QMessageBox.question(
                self,
                "Не хватает данных",
                "В форме не хватает: " + ", ".join(missing) + ".\n\nПродолжить всё равно?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.No:
                return

        self._save_settings()
        self.progress_bar.setValue(0)
        self._open_process_dialog(
            "rulate_media_create",
            "Rulate → RanobeLib: создание карточки",
            can_stop=True,
            stop_fn=self._stop_rulate_media_create,
        )
        self.btn_create_ranobelib_media.setEnabled(False)

        self._rulate_media_worker = RulateToRanobeCreateWorker(rulate_url, options=options)
        self._rulate_media_worker.log_signal.connect(
            lambda level, msg: self._process_log("rulate_media_create", level, msg)
        )
        self._rulate_media_worker.progress_signal.connect(
            lambda val: self._process_progress("rulate_media_create", val)
        )
        self._rulate_media_worker.finished_signal.connect(self._on_rulate_media_create_finished)
        self._rulate_media_worker.start()

    def _stop_rulate_media_create(self):
        worker = getattr(self, "_rulate_media_worker", None)
        if worker:
            self._process_log("rulate_media_create", "WARNING", "Останавливаю создание карточки...")
            worker.stop()

    def _on_rulate_media_create_finished(self):
        self.btn_create_ranobelib_media.setEnabled(True)
        self._finish_process_dialog("rulate_media_create")

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
            skip_after=self._last_lib_chapter,
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
        selected_infos = []
        for i in range(self.rulate_list_widget.count()):
            item = self.rulate_list_widget.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                ch = item.data(Qt.ItemDataRole.UserRole)
                if ch and ch.get("id"):
                    selected_ids.append(ch["id"])
                    selected_infos.append(dict(ch))

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
            chapter_infos=selected_infos,
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
        search_role = Qt.ItemDataRole.UserRole + 1
        self.chapters_list_widget.setUpdatesEnabled(False)
        self.chapters_list_widget.clear()
        self.last_clicked_row = -1
        self.search_input.clear()  # Feature 3: сбросить фильтр при загрузке нового файла
        has_fractional = False
        for ch in self.chapters_to_upload:
            label = f"{ch}  [{ch.content_length:,} зн.]"
            item = QListWidgetItem(label)
            item.setCheckState(Qt.CheckState.Checked)
            item.setData(Qt.ItemDataRole.UserRole, ch)
            item.setData(search_role, f"{ch.title} {format_num(ch.number)} {ch.volume}".lower())
            self.chapters_list_widget.addItem(item)
            if ch.number != int(ch.number):
                has_fractional = True
        self.chapters_list_widget.setUpdatesEnabled(True)
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
        self.chapters_list_widget.setUpdatesEnabled(False)
        try:
            for i in range(self.chapters_list_widget.count()):
                self.chapters_list_widget.item(i).setCheckState(Qt.CheckState.Checked)
        finally:
            self.chapters_list_widget.setUpdatesEnabled(True)

    def _deselect_all(self):
        self.chapters_list_widget.setUpdatesEnabled(False)
        try:
            for i in range(self.chapters_list_widget.count()):
                self.chapters_list_widget.item(i).setCheckState(Qt.CheckState.Unchecked)
        finally:
            self.chapters_list_widget.setUpdatesEnabled(True)

    def _invert_selection(self):
        self.chapters_list_widget.setUpdatesEnabled(False)
        try:
            for i in range(self.chapters_list_widget.count()):
                item = self.chapters_list_widget.item(i)
                new_state = (
                    Qt.CheckState.Unchecked
                    if item.checkState() == Qt.CheckState.Checked
                    else Qt.CheckState.Checked
                )
                item.setCheckState(new_state)
        finally:
            self.chapters_list_widget.setUpdatesEnabled(True)

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
            item.setData(
                Qt.ItemDataRole.UserRole + 1,
                f"{chapter.title} {format_num(chapter.number)} {chapter.volume}".lower(),
            )

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


