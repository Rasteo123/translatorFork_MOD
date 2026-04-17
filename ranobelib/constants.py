import logging
import re
from datetime import datetime
from pathlib import Path

APP_NAME = "RanobeLib Uploader"
APP_VERSION = "12.1"
SETTINGS_ORG = "RanobeTools"
SETTINGS_APP = "RanobeUploader"

APP_DATA_DIR = Path.home() / ".ranobelib_uploader"
APP_DATA_DIR.mkdir(exist_ok=True)
BROWSER_PROFILE_DIR = APP_DATA_DIR / "user_data_profile"
BROWSER_RULATE_DIR = APP_DATA_DIR / "user_data_rulate"
BROWSER_PROFILE_DIR.mkdir(exist_ok=True)
BROWSER_RULATE_DIR.mkdir(exist_ok=True)

LOG_DIR = APP_DATA_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

log_filename = LOG_DIR / f"log_{datetime.now():%Y-%m-%d}.txt"
logging.basicConfig(
    filename=str(log_filename),
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    encoding="utf-8",
)

MAX_RETRIES = 3
RETRY_DELAY_SEC = 5

SELECTORS = {
    "volume_input":     'input[placeholder="Том"]',
    "chapter_input":    'input[placeholder="Глава"]',
    "title_input":      'input[placeholder="Название главы"]',
    "editor_area":      ".ProseMirror",
    "submit_btn":       ".chapter-form__buttons",
    "clock_btn":        'button:has(svg[data-icon="clock"])',
    "gear_btn":         'button:has(svg[data-icon="gear"])',
    "popover":          ".tippy-box",
    "price_input":      "input.form-input__field",
    "arrow_up":         'svg[data-icon="chevron-up"]',
    "arrow_down":       'svg[data-icon="chevron-down"]',
    "month_arrow_left": 'svg[data-icon="arrow-left"]',
    "month_arrow_right":'svg[data-icon="arrow-right"]',
}

BROWSER_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-infobars",
]

RUS_MONTHS = {
    "январь": 1, "февраль": 2, "март": 3, "апрель": 4,
    "май": 5, "июнь": 6, "июль": 7, "август": 8,
    "сентябрь": 9, "октябрь": 10, "ноябрь": 11, "декабрь": 12,
}

URL_PATTERN = re.compile(
    r"^https?://(?:ranobelib\.me|ranobelib\.com)/ru/book/.+/add-chapter",
    re.IGNORECASE,
)

RULATE_URL_PATTERN = re.compile(
    r"^https?://tl\.rulate\.ru/book/(\d+)",
    re.IGNORECASE,
)

# ─── Стили ───────────────────────────────────────────────────────────────────

LIGHT_STYLE = """
    QMainWindow { background-color: #fafafa; }
    QGroupBox { font-weight: bold; border: 1px solid #ccc; border-radius: 6px;
                margin-top: 8px; padding-top: 14px; }
    QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 6px; }
    QPushButton { border: 1px solid #aaa; border-radius: 4px; padding: 5px 12px;
                  background-color: #f5f5f5; }
    QPushButton:hover { background-color: #e8e8e8; }
    QPushButton:disabled { color: #aaa; }
    QProgressBar { border: 1px solid #ccc; border-radius: 4px; text-align: center; }
    QProgressBar::chunk { background-color: #4caf50; border-radius: 3px; }
    QTextEdit#logArea { background-color: #f0f0f0; font-family: Consolas, monospace;
                        font-size: 11px; border: 1px solid #ccc; border-radius: 4px; }
    QListWidget { border: 1px solid #ccc; border-radius: 4px; }
    QStatusBar { font-size: 11px; }
"""

DARK_STYLE = """
    QMainWindow { background-color: #1e1e1e; color: #d4d4d4; }
    QWidget { background-color: #1e1e1e; color: #d4d4d4; }
    QGroupBox { font-weight: bold; border: 1px solid #444; border-radius: 6px;
                margin-top: 8px; padding-top: 14px; color: #d4d4d4; }
    QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 6px; }
    QPushButton { border: 1px solid #555; border-radius: 4px; padding: 5px 12px;
                  background-color: #2d2d2d; color: #d4d4d4; }
    QPushButton:hover { background-color: #3a3a3a; }
    QPushButton:disabled { color: #666; }
    QLineEdit, QSpinBox, QDateTimeEdit, QComboBox { background-color: #2d2d2d; color: #d4d4d4;
                                          border: 1px solid #555; border-radius: 3px;
                                          padding: 3px; }
    QProgressBar { border: 1px solid #555; border-radius: 4px; text-align: center;
                   background-color: #2d2d2d; color: #d4d4d4; }
    QProgressBar::chunk { background-color: #388e3c; border-radius: 3px; }
    QTextEdit#logArea { background-color: #1a1a1a; color: #cccccc;
                        font-family: Consolas, monospace; font-size: 11px;
                        border: 1px solid #444; border-radius: 4px; }
    QListWidget { background-color: #252526; color: #d4d4d4;
                  border: 1px solid #444; border-radius: 4px; }
    QListWidget::item { padding: 2px; }
    QListWidget::item:selected { background-color: #094771; }
    QCheckBox { color: #d4d4d4; }
    QLabel { color: #d4d4d4; }
    QStatusBar { font-size: 11px; color: #888; }
    QMenu { background-color: #2d2d2d; color: #d4d4d4; border: 1px solid #555; }
    QMenu::item:selected { background-color: #094771; }
"""

