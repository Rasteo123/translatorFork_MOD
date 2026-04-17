from datetime import datetime

from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from models import ChapterData
from utils import format_num

class PreviewDialog(QDialog):
    def __init__(self, chapter: ChapterData, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Предпросмотр: {chapter}")
        self.resize(600, 500)

        layout = QVBoxLayout(self)

        info = QLabel(
            f"Том: {chapter.volume}  |  Глава: {format_num(chapter.number)}  |  "
            f"Символов: {chapter.content_length:,}"
        )
        layout.addWidget(info)

        text_view = QTextEdit()
        text_view.setReadOnly(True)
        if chapter.content.strip().startswith("<"):
            text_view.setHtml(chapter.content)
        else:
            text_view.setPlainText(chapter.content)
        layout.addWidget(text_view)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)


# ─── Главное окно ───────────────────────────────────────────────────────────

class ProcessDialog(QDialog):
    """Отдельное окно для конкретного процесса."""
    stop_requested = pyqtSignal()
    AUTO_CLOSE_DELAY_MS = 300

    def __init__(self, title: str, can_stop: bool = False, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(700, 460)

        layout = QVBoxLayout(self)

        self.progress = QProgressBar()
        self.progress.setValue(0)
        layout.addWidget(self.progress)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        layout.addWidget(self.log_view)

        btns = QHBoxLayout()
        btns.addStretch()

        self.btn_stop = None
        if can_stop:
            self.btn_stop = QPushButton("Стоп")
            self.btn_stop.clicked.connect(self.stop_requested.emit)
            btns.addWidget(self.btn_stop)

        self.btn_close = QPushButton("Закрыть")
        self.btn_close.setEnabled(False)
        self.btn_close.clicked.connect(self.close)
        btns.addWidget(self.btn_close)
        layout.addLayout(btns)

    def append_log(self, level: str, message: str):
        ts = datetime.now().strftime("%H:%M:%S")
        colors = {"ERROR": "#D32F2F", "WARNING": "#F57C00", "SUCCESS": "#388E3C"}
        color = colors.get(level, "#000")
        self.log_view.append(
            f'<span style="color:#888;">[{ts}]</span> '
            f'<b style="color:{color};">[{level}]</b> {message}'
        )

    def set_progress(self, value: int):
        self.progress.setValue(max(0, min(100, int(value))))

    def mark_finished(self):
        if self.btn_stop:
            self.btn_stop.setEnabled(False)
        self.btn_close.setEnabled(True)
        QTimer.singleShot(self.AUTO_CLOSE_DELAY_MS, self.close)


