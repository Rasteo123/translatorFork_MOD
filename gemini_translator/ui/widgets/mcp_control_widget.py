from __future__ import annotations

from dataclasses import dataclass

from PyQt6 import QtCore, QtWidgets
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from gemini_translator.ui import theme_manager


@dataclass(frozen=True)
class McpStatusSnapshot:
    running: bool
    detail: str = "stdio + local daemon"
    error: str | None = None


class McpControlBackend:
    def status(self) -> McpStatusSnapshot:
        from gemini_translator.mcp.client import DaemonClientError, load_client

        try:
            payload = load_client().status()
        except DaemonClientError as exc:
            if str(exc) == "daemon is not running":
                return McpStatusSnapshot(running=False, detail="stdio + local daemon")
            return McpStatusSnapshot(running=False, detail="stdio + local daemon", error=str(exc))
        daemon = payload.get("daemon", {}) if isinstance(payload, dict) else {}
        host = daemon.get("host", "127.0.0.1")
        port = daemon.get("port", "")
        return McpStatusSnapshot(running=True, detail=f"{host}:{port}")

    def start(self) -> McpStatusSnapshot:
        from gemini_translator.mcp.client import ensure_daemon_process

        client = ensure_daemon_process()
        payload = client.status()
        daemon = payload.get("daemon", {}) if isinstance(payload, dict) else {}
        host = daemon.get("host", "127.0.0.1")
        port = daemon.get("port", "")
        return McpStatusSnapshot(running=True, detail=f"{host}:{port}")

    def stop(self) -> McpStatusSnapshot:
        from gemini_translator.mcp.client import DaemonClientError, load_client

        try:
            load_client().shutdown()
        except DaemonClientError as exc:
            return McpStatusSnapshot(running=False, detail="stdio + local daemon", error=str(exc))
        return McpStatusSnapshot(running=False, detail="stdio + local daemon")

    def codex_config(self) -> str:
        from gemini_translator.mcp.client_install import build_config_snippet

        snippet = build_config_snippet("codex")
        return str(snippet.get("text", ""))


class McpActionWorker(QtCore.QObject):
    finished = QtCore.pyqtSignal(object)

    def __init__(self, backend: McpControlBackend, action: str, running: bool):
        super().__init__()
        self.backend = backend
        self.action = action
        self.running = running

    @QtCore.pyqtSlot()
    def run(self) -> None:
        try:
            if self.action == "status":
                snapshot = self.backend.status()
            elif self.action == "toggle" and self.running:
                snapshot = self.backend.stop()
            elif self.action == "toggle":
                snapshot = self.backend.start()
            else:
                snapshot = McpStatusSnapshot(running=False, detail="Неизвестное действие", error=str(self.action))
        except Exception as exc:
            snapshot = McpStatusSnapshot(running=False, detail="ошибка MCP", error=str(exc))
        self.finished.emit(snapshot)


_ACTIVE_THREADS: set[QtCore.QThread] = set()
_ACTIVE_WORKERS: dict[QtCore.QThread, McpActionWorker] = {}


def _forget_finished_mcp_workers() -> None:
    for thread in list(_ACTIVE_THREADS):
        if not thread.isRunning():
            _ACTIVE_THREADS.discard(thread)
            _ACTIVE_WORKERS.pop(thread, None)


class McpControlWidget(QtWidgets.QFrame):
    refresh_requested = QtCore.pyqtSignal()

    def __init__(self, parent=None, *, backend=None):
        super().__init__(parent)
        self.backend = backend or McpControlBackend()
        self._running = False
        self._worker_thread = None
        self._worker = None
        self._closing = False
        self.setObjectName("mcpControlCard")
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Minimum, QtWidgets.QSizePolicy.Policy.Minimum)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(1)

        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(4)

        title_label = QLabel("MCP")
        title_label.setObjectName("keyStatusTitle")
        status_row.addWidget(title_label)

        self.status_value_label = QLabel("Выключен")
        self.status_value_label.setObjectName("keyStatusMetricValue")
        status_row.addWidget(self.status_value_label)
        status_row.addStretch(1)
        text_col.addLayout(status_row)

        self.detail_label = QLabel("stdio + local daemon")
        self.detail_label.setObjectName("mutedLabel")
        text_col.addWidget(self.detail_label)
        layout.addLayout(text_col)

        self.action_button = QPushButton("Запустить")
        self.action_button.setObjectName("mcpActionButton")
        self.action_button.setFixedHeight(30)
        self.action_button.setFixedWidth(84)
        layout.addWidget(self.action_button)

        self.config_button = QPushButton("Codex config")
        self.config_button.setObjectName("mcpConfigButton")
        self.config_button.setFixedHeight(30)
        self.config_button.setFixedWidth(90)
        layout.addWidget(self.config_button)

        self.apply_status(McpStatusSnapshot(running=False))
        self.action_button.clicked.connect(lambda: self._dispatch_action("toggle"))
        self.config_button.clicked.connect(self.copy_codex_config)

    def apply_status(self, snapshot: McpStatusSnapshot) -> None:
        self._running = bool(snapshot.running) and not snapshot.error
        if snapshot.error:
            self.status_value_label.setText("Ошибка")
            self.setToolTip(snapshot.error)
        elif self._running:
            self.status_value_label.setText("Запущен")
            self.setToolTip("")
        else:
            self.status_value_label.setText("Выключен")
            self.setToolTip("")
        self.detail_label.setText(snapshot.detail or "stdio + local daemon")
        self.action_button.setText("Остановить" if self._running else "Запустить")
        self._apply_button_style()

    def _execute_action_sync(self, action: str) -> McpStatusSnapshot:
        try:
            if action == "status":
                snapshot = self.backend.status()
            elif action == "toggle" and self._running:
                snapshot = self.backend.stop()
            elif action == "toggle":
                snapshot = self.backend.start()
            else:
                snapshot = McpStatusSnapshot(running=False, detail="Неизвестное действие", error=str(action))
        except Exception as exc:
            snapshot = McpStatusSnapshot(running=False, detail="ошибка MCP", error=str(exc))
        self.apply_status(snapshot)
        return snapshot

    def _dispatch_action(self, action: str) -> None:
        if self._worker_thread is not None:
            return
        self._closing = False
        self.action_button.setEnabled(False)
        thread = QtCore.QThread()
        worker = McpActionWorker(self.backend, action, self._running)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(thread.quit)
        worker.finished.connect(self._on_worker_finished)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(_forget_finished_mcp_workers)
        _ACTIVE_THREADS.add(thread)
        _ACTIVE_WORKERS[thread] = worker
        self._worker_thread = thread
        self._worker = worker
        thread.start()

    def _on_worker_finished(self, snapshot: McpStatusSnapshot) -> None:
        thread = self._worker_thread
        self._worker_thread = None
        self._worker = None
        if not self._closing:
            self.apply_status(snapshot)
        self.action_button.setEnabled(True)
        if thread is not None:
            thread.quit()

    def _wait_for_worker(self) -> None:
        thread = self._worker_thread
        worker = self._worker
        if thread is None:
            return
        if worker is not None:
            try:
                worker.finished.disconnect(self._on_worker_finished)
            except TypeError:
                pass
        if thread.isRunning():
            thread.quit()
            thread.wait()
        _forget_finished_mcp_workers()
        self._worker_thread = None
        self._worker = None
        self.action_button.setEnabled(True)

    def closeEvent(self, event) -> None:
        self._closing = True
        self._wait_for_worker()
        super().closeEvent(event)

    def refresh_status(self) -> McpStatusSnapshot:
        return self._execute_action_sync("status")

    def copy_codex_config(self) -> str:
        try:
            text = self.backend.codex_config()
        except Exception as exc:
            self.detail_label.setText("ошибка config")
            self.setToolTip(str(exc))
            self.status_value_label.setText("Запущен" if self._running else "Выключен")
            self.action_button.setText("Остановить" if self._running else "Запустить")
            self._apply_button_style()
            return ""
        QtWidgets.QApplication.clipboard().setText(text)
        self.config_button.setToolTip("Codex config скопирован")
        return text

    def _apply_button_style(self) -> None:
        color = theme_manager.color("danger") if self._running else theme_manager.color("success")
        self.action_button.setStyleSheet(
            "QPushButton { "
            f"background-color: {color}; "
            f"color: {theme_manager.color('accent_text')}; "
            "font-weight: bold; padding: 4px 6px; border-radius: 4px; "
            "}"
        )
