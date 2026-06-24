# gemini_translator/ui/widgets/log_widget.py

import html
import os
import time
import uuid

from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtCore import pyqtSlot
from PyQt6.QtWidgets import QCheckBox, QHBoxLayout, QTextBrowser, QTextEdit, QVBoxLayout, QWidget

from gemini_translator.ui import theme_manager


LOG_STYLES = [
    {
        'keywords': ['[FILTER]', '[API BLOCK]', 'CONTENT FILTER', 'ЗАБЛОКИРОВАНА', '🛡️ ФИЛЬТР', 'CONTENT_FILTER'],
        'light_color': "#7d3c98",
        'dark_color': "#c77dff",
        'bold': True
    },
    {
        'keywords': ['[VALIDATION]', 'ВАЛИДАЦИЯ', 'VALIDATION FAILED', 'НЕ ПРОШЕЛ ВАЛИДАЦИЮ', '📋 ВАЛИДАЦИЯ', 'VALIDATION'],
        'light_color': "#117a65",
        'dark_color': "#2dd4bf",
        'bold': True
    },
    {
        'keywords': ['[WARN]', '[WARNING]', 'ПРЕДУПРЕЖДЕНИЕ', '❗️', 'NETWORK', 'PARTIAL_GENERATION'],
        'palette_key': "warning",
        'bold': False
    },
    {
        'keywords': ['[SUCCESS]', 'УСПЕШНО', 'ГОТОВО', '✅', 'СЕССИЯ УСПЕШНО ЗАВЕРШЕНА'],
        'palette_key': "success",
        'bold': True
    },
    {
        'keywords': ['[CANCELLED]', '[SKIP]', 'ОТМЕНЕНО', '[INFO]', 'СЕССИЯ ОСТАНОВЛЕНА', 'CANCEL'],
        'palette_key': "info",
        'bold': False
    },
    {
        'keywords': ['[RATE LIMIT]', 'QUOTA EXCEEDED', 'QUOTA_EXCEEDED', 'ИСЧЕРПАН', 'TEMPORARY_LIMIT'],
        'light_color': "#c2185b",
        'dark_color': "#ff6aa2",
        'bold': True
    },
    {
        'keywords': [
            '[FAIL]', '[ERROR]', '[FATAL]', '[CRITICAL]', 'ОШИБКА', '❌ ОШИБКА', 'ОКОНЧАТЕЛЬНЫЙ ПРОВАЛ',
            'GEOBLOCK', 'MODEL_NOT_FOUND', 'API_ERROR'
        ],
        'palette_key': "danger",
        'bold': True
    },
    {
        'keywords': ['▶▶▶', '■■■', '[MANAGER]', "[TASK]"],
        'light_color': "#5b6470",
        'dark_color': "#b8c2cc",
        'bold': True
    }
]
MAX_LOG_BLOCKS = 1200
MAX_STORED_DETAILS = 200
MAX_DETAIL_TEXT_CHARS = 16000
MAX_PENDING_LOG_MESSAGES = 2000
MAX_LOG_FLUSH_BATCH_SIZE = 300
LOG_FLUSH_INTERVAL_MS = 1000
# Smooth catch-up: when a backlog larger than one chunk is waiting (e.g. the user
# just switched to the log tab after being away), drain it in small chunks at a
# fast cadence so lines stream in instead of arriving in one jerk. Bounded,
# one-time burst that self-terminates once drained; the live trickle stays calm.
CATCHUP_FLUSH_BATCH_SIZE = 25
CATCHUP_FLUSH_INTERVAL_MS = 60


def _color_luminance(color: str) -> float:
    normalized = color.strip()
    if len(normalized) != 7 or not normalized.startswith("#"):
        return 0.0
    try:
        red = int(normalized[1:3], 16)
        green = int(normalized[3:5], 16)
        blue = int(normalized[5:7], 16)
    except ValueError:
        return 0.0
    return ((0.2126 * red) + (0.7152 * green) + (0.0722 * blue)) / 255.0


def _log_palette_is_light(palette: dict) -> bool:
    return _color_luminance(str(palette.get("window_bg") or "#000000")) >= 0.5


def _resolve_log_color(style_rule: dict, palette: dict) -> str | None:
    palette_key = style_rule.get("palette_key")
    if palette_key:
        color = palette.get(palette_key)
        if isinstance(color, str) and color:
            return color

    if _log_palette_is_light(palette):
        return style_rule.get("light_color") or style_rule.get("color")
    return style_rule.get("dark_color") or style_rule.get("color")


class LogWidget(QWidget):
    """Виджет для отображения цветного лога выполнения с автопрокруткой."""

    def __init__(self, parent=None, event_bus=None):
        super().__init__(parent)
        self._details_map = {}
        self._uses_topic_subscription = False
        self._pending_log_data = []
        self._init_ui()
        self._log_flush_timer = QtCore.QTimer(self)
        self._log_flush_timer.setSingleShot(True)
        self._log_flush_timer.setTimerType(QtCore.Qt.TimerType.CoarseTimer)
        self._log_flush_timer.timeout.connect(self._flush_pending_messages)

        self.bus = event_bus
        if self.bus is None:
            app = QtWidgets.QApplication.instance()
            if hasattr(app, 'event_bus'):
                self.bus = app.event_bus

        if self.bus and hasattr(self.bus, "subscribe"):
            self.bus.subscribe("log_message", self._on_log_message)
            self._uses_topic_subscription = True
        elif self.bus and hasattr(self.bus, "event_posted"):
            self.bus.event_posted.connect(self.on_event)
        else:
            print("[LogWidget WARN] Шина событий не предоставлена и не нашел его в QApplication. Логи не будут отображаться.")

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.log_view = QTextBrowser()
        self.log_view.setReadOnly(True)
        self.log_view.setOpenLinks(False)
        self.log_view.setUndoRedoEnabled(False)
        self.log_view.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.log_view.document().setMaximumBlockCount(MAX_LOG_BLOCKS)
        self.log_view.anchorClicked.connect(self._on_anchor_clicked)

        controls_layout = QHBoxLayout()
        self.autoscroll_checkbox = QCheckBox("Автопрокрутка")
        self.autoscroll_checkbox.setChecked(True)
        controls_layout.addWidget(self.autoscroll_checkbox)
        controls_layout.addStretch()

        layout.addWidget(self.log_view)
        layout.addLayout(controls_layout)

    @pyqtSlot(dict)
    def on_event(self, event_data: dict):
        if event_data.get('event') == 'log_message':
            self._on_log_message(event_data)

    def _on_log_message(self, event_data: dict):
        data = event_data.get('data', {})
        self.append_message(data)

    def append_message(self, data: dict):
        if not isinstance(data, dict):
            return

        message = data.get('message')
        if not isinstance(message, str) or not message.strip():
            return

        priority = data.get('priority', 'normal')
        self._queue_log_message(data)
        self._schedule_log_flush(0 if priority == 'final' else LOG_FLUSH_INTERVAL_MS)

    def _queue_log_message(self, data: dict):
        self._pending_log_data.append(dict(data))
        if len(self._pending_log_data) <= MAX_PENDING_LOG_MESSAGES:
            return

        dropped_count = len(self._pending_log_data) - MAX_PENDING_LOG_MESSAGES + 1
        del self._pending_log_data[:dropped_count]
        notice = {
            'message': f"[WARN] Пропущено {dropped_count} сообщений лога: интерфейс не успевал их отрисовать."
        }
        self._pending_log_data.insert(0, notice)

    def _schedule_log_flush(self, delay_ms: int = LOG_FLUSH_INTERVAL_MS):
        if self._log_flush_timer.isActive():
            return
        # Hidden QTabWidget pages still receive log events; rendering them wastes CPU.
        if not self.isVisible():
            return
        self._log_flush_timer.start(max(0, int(delay_ms)))

    def _show_details_dialog(self, title: str, text: str):
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle(title)
        dialog.setMinimumSize(900, 650)

        layout = QVBoxLayout(dialog)

        viewer = QtWidgets.QPlainTextEdit(dialog)
        viewer.setReadOnly(True)
        viewer.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap)
        viewer.setPlainText(text)
        viewer.setFont(QtGui.QFont("Consolas", 10))
        layout.addWidget(viewer)

        buttons = QHBoxLayout()
        copy_button = QtWidgets.QPushButton("Скопировать", dialog)
        close_button = QtWidgets.QPushButton("Закрыть", dialog)
        copy_button.clicked.connect(lambda: QtWidgets.QApplication.clipboard().setText(text))
        close_button.clicked.connect(dialog.accept)
        buttons.addWidget(copy_button)
        buttons.addStretch()
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

        dialog.exec()

    def _on_anchor_clicked(self, url: QtCore.QUrl):
        scheme = url.scheme()
        if scheme not in {"logdetail", "logfile"}:
            return

        payload_id = url.toString().split(":", 1)[-1]
        payload = self._details_map.get(payload_id)
        if not payload:
            return

        if scheme == "logdetail":
            self._show_details_dialog(payload['title'], payload['text'])
            return

        file_path = payload.get('path')
        if not isinstance(file_path, str) or not file_path.strip():
            return

        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(file_path))

    def _add_html_to_log(self, data: dict):
        self._insert_html_batch(self._build_log_html(data))

    def _flush_pending_messages(self):
        if not self._pending_log_data:
            return

        catching_up = len(self._pending_log_data) > CATCHUP_FLUSH_BATCH_SIZE
        batch_size = CATCHUP_FLUSH_BATCH_SIZE if catching_up else MAX_LOG_FLUSH_BATCH_SIZE

        batch = self._pending_log_data[:batch_size]
        del self._pending_log_data[:batch_size]

        html_batch = "".join(self._build_log_html(data) for data in batch)
        if html_batch:
            self._insert_html_batch(html_batch)

        if self._pending_log_data:
            self._schedule_log_flush(
                CATCHUP_FLUSH_INTERVAL_MS if catching_up else LOG_FLUSH_INTERVAL_MS
            )

    def _build_log_html(self, data: dict) -> str:
        message = data.get('message', '')
        if message == "---SEPARATOR---":
            return "<br><hr style='border: 1px dashed #4d5666;'><br>"

        current_time = time.strftime("%H:%M:%S", time.localtime())
        formatted_line = f"[{current_time}] {message}"

        color = None
        bold = False
        msg_upper = message.upper()
        palette = theme_manager.palette()

        for style_rule in LOG_STYLES:
            if any(keyword in msg_upper for keyword in style_rule['keywords']):
                color = _resolve_log_color(style_rule, palette)
                bold = style_rule.get('bold', False)
                break

        escaped_line = html.escape(formatted_line)
        style_parts = []
        if color:
            style_parts.append(f"color: {color};")
        if bold:
            style_parts.append("font-weight: bold;")

        html_line = f"<span style='{' '.join(style_parts)}'>{escaped_line}</span>"

        links_html = []

        details_text = data.get('details_text')
        if isinstance(details_text, str) and details_text.strip():
            details_text = self._truncate_details_text(details_text)
            detail_id = uuid.uuid4().hex
            self._details_map[detail_id] = {
                'title': data.get('details_title') or "Детали сообщения",
                'text': details_text
            }
            self._trim_details_map()
            links_html.append(f"<a href='logdetail:{detail_id}'>[details]</a>")

        file_path = data.get('file_path')
        if isinstance(file_path, str) and file_path.strip():
            file_id = uuid.uuid4().hex
            self._details_map[file_id] = {'path': file_path}
            self._trim_details_map()
            default_label = "open folder" if os.path.isdir(file_path) else "open log"
            file_label = html.escape(str(data.get('file_label') or default_label))
            links_html.append(f"<a href='logfile:{file_id}'>[{file_label}]</a>")

        if links_html:
            html_line += " " + " ".join(links_html)
        html_line += "<br>"
        return html_line

    def _insert_html_batch(self, html_batch: str):
        if not html_batch:
            return

        self.log_view.setUpdatesEnabled(False)
        try:
            cursor = self.log_view.textCursor()
            cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)
            cursor.beginEditBlock()
            cursor.insertHtml(html_batch)
            cursor.endEditBlock()
        finally:
            self.log_view.setUpdatesEnabled(True)

        if self.autoscroll_checkbox.isChecked():
            self.log_view.verticalScrollBar().setValue(self.log_view.verticalScrollBar().maximum())

    def clear(self):
        """Очищает лог."""
        self.log_view.clear()
        self._details_map.clear()
        self._pending_log_data.clear()
        self._log_flush_timer.stop()

    def hideEvent(self, event):
        self._log_flush_timer.stop()
        super().hideEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        if self._pending_log_data:
            # If a backlog piled up while hidden, start the smooth catch-up
            # promptly (fast cadence) instead of waiting a full second.
            catching_up = len(self._pending_log_data) > CATCHUP_FLUSH_BATCH_SIZE
            self._schedule_log_flush(
                CATCHUP_FLUSH_INTERVAL_MS if catching_up else LOG_FLUSH_INTERVAL_MS
            )

    def closeEvent(self, event):
        """Отписываемся от шины при закрытии/уничтожении виджета."""
        bus = getattr(self, "bus", None)
        if bus:
            try:
                if self._uses_topic_subscription and hasattr(bus, "unsubscribe"):
                    bus.unsubscribe("log_message", self._on_log_message)
                elif hasattr(bus, "event_posted"):
                    bus.event_posted.disconnect(self.on_event)
            except (TypeError, RuntimeError, ValueError):
                pass
        self._log_flush_timer.stop()
        super().closeEvent(event)

    def _trim_details_map(self):
        while len(self._details_map) > MAX_STORED_DETAILS:
            oldest_key = next(iter(self._details_map), None)
            if oldest_key is None:
                break
            self._details_map.pop(oldest_key, None)

    def _truncate_details_text(self, details_text: str) -> str:
        normalized_text = details_text.strip()
        if len(normalized_text) <= MAX_DETAIL_TEXT_CHARS:
            return normalized_text
        omitted = len(normalized_text) - MAX_DETAIL_TEXT_CHARS
        return normalized_text[:MAX_DETAIL_TEXT_CHARS].rstrip() + f"\n\n[details truncated: {omitted} chars omitted]"
