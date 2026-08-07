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
    connected_clients: int = 0


def _snapshot_from_status_payload(payload) -> McpStatusSnapshot:
    daemon = payload.get("daemon", {}) if isinstance(payload, dict) else {}
    host = daemon.get("host", "127.0.0.1")
    port = daemon.get("port", "")
    clients = payload.get("mcp_clients", {}) if isinstance(payload, dict) else {}
    try:
        connected_clients = int(clients.get("connected") or 0)
    except (TypeError, ValueError, OverflowError):
        connected_clients = 0
    return McpStatusSnapshot(
        running=True,
        detail=f"{host}:{port}",
        connected_clients=max(0, connected_clients),
    )


def _daemon_error_means_not_running(message: str) -> bool:
    text = str(message or "").lower()
    markers = (
        "daemon is not running",
        "connection refused",
        "connection reset",
        "connection aborted",
        "timed out",
        "timeout",
        "failed to establish",
        "actively refused",
        "errno 61",
        "errno 111",
        "winerror 10061",
        "urlopen error",
    )
    return any(marker in text for marker in markers)


class McpControlBackend:
    def status(self) -> McpStatusSnapshot:
        from gemini_translator.mcp.client import DaemonClientError, load_client

        try:
            payload = load_client().status()
        except DaemonClientError as exc:
            if _daemon_error_means_not_running(str(exc)):
                return McpStatusSnapshot(running=False, detail="stdio + local daemon")
            return McpStatusSnapshot(running=False, detail="stdio + local daemon", error=str(exc))
        return _snapshot_from_status_payload(payload)

    def start(self) -> McpStatusSnapshot:
        from gemini_translator.mcp.client import ensure_daemon_process

        client = ensure_daemon_process()
        payload = client.status()
        return _snapshot_from_status_payload(payload)

    def stop(self) -> McpStatusSnapshot:
        from gemini_translator.mcp.client import DaemonClientError, load_client

        try:
            load_client().shutdown()
        except DaemonClientError as exc:
            if _daemon_error_means_not_running(str(exc)):
                return McpStatusSnapshot(running=False, detail="stdio + local daemon")
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


def _forget_mcp_worker(thread: QtCore.QThread) -> None:
    _ACTIVE_THREADS.discard(thread)
    _ACTIVE_WORKERS.pop(thread, None)


class McpControlWidget(QtWidgets.QFrame):
    refresh_requested = QtCore.pyqtSignal()
    status_changed = QtCore.pyqtSignal(object)

    def __init__(self, parent=None, *, backend=None):
        super().__init__(parent)
        self.backend = backend or McpControlBackend()
        self._running = False
        self._last_status = McpStatusSnapshot(running=False)
        self._auto_refresh_enabled = False
        self._stop_on_app_quit = False
        self._worker_thread = None
        self._worker = None
        self._worker_action = None
        self._worker_was_running = False
        self._pending_worker_result = None
        self._closing = False
        self.setObjectName("mcpControlCard")
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Minimum, QtWidgets.QSizePolicy.Policy.Minimum)

        self._status_timer = QtCore.QTimer(self)
        self._status_timer.setInterval(2500)
        self._status_timer.timeout.connect(self._poll_status_if_running)

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
        self.status_value_label.setMaximumWidth(78)
        status_row.addWidget(self.status_value_label)
        status_row.addStretch(1)
        text_col.addLayout(status_row)

        self.detail_label = QLabel("stdio + local daemon")
        self.detail_label.setObjectName("mutedLabel")
        self.detail_label.setMaximumWidth(112)
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
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._on_app_about_to_quit)

    def sizeHint(self) -> QtCore.QSize:
        hint = super().sizeHint()
        return QtCore.QSize(min(hint.width(), 320), hint.height())

    def minimumSizeHint(self) -> QtCore.QSize:
        hint = super().minimumSizeHint()
        return QtCore.QSize(min(hint.width(), 300), hint.height())

    def apply_status(self, snapshot: McpStatusSnapshot) -> None:
        self._last_status = snapshot
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
        detail_text = snapshot.detail or "stdio + local daemon"
        if self._running and snapshot.connected_clients:
            client_label = "клиент" if snapshot.connected_clients == 1 else "клиентов"
            detail_text = f"{detail_text} · {client_label}: {snapshot.connected_clients}"
        self.detail_label.setText(detail_text)
        self.action_button.setText("Остановить" if self._running else "Запустить")
        self._apply_button_style()
        self._sync_status_timer()
        self.status_changed.emit(snapshot)

    def has_connected_client(self) -> bool:
        return self._running and self._last_status.connected_clients > 0

    def set_auto_refresh_enabled(self, enabled: bool) -> None:
        self._auto_refresh_enabled = bool(enabled)
        self._sync_status_timer()

    def _sync_status_timer(self) -> None:
        should_run = self._auto_refresh_enabled and self._running and self._worker_thread is None
        if should_run and not self._status_timer.isActive():
            self._status_timer.start()
        elif not should_run and self._status_timer.isActive():
            self._status_timer.stop()

    def _poll_status_if_running(self) -> None:
        if not self._running or self._worker_thread is not None:
            return
        self.refresh_status()

    def _execute_action_sync(self, action: str) -> McpStatusSnapshot:
        was_running = self._running
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
        self._update_stop_on_quit_policy(action, was_running, snapshot)
        return snapshot

    def _update_stop_on_quit_policy(self, action: str, was_running: bool, snapshot: McpStatusSnapshot) -> None:
        if action != "toggle" or snapshot.error:
            return
        if not was_running and snapshot.running:
            self._stop_on_app_quit = True
        elif was_running and not snapshot.running:
            self._stop_on_app_quit = False

    def _dispatch_action(self, action: str) -> None:
        if self._worker_thread is not None:
            return
        self._closing = False
        if action != "status":
            self.action_button.setEnabled(False)
        thread = QtCore.QThread()
        worker = McpActionWorker(self.backend, action, self._running)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(thread.quit)
        worker.finished.connect(self._on_worker_finished)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(lambda thread=thread: self._on_worker_thread_finished(thread))
        thread.finished.connect(thread.deleteLater)
        _ACTIVE_THREADS.add(thread)
        _ACTIVE_WORKERS[thread] = worker
        self._worker_thread = thread
        self._worker = worker
        self._worker_action = action
        self._worker_was_running = self._running
        self._pending_worker_result = None
        self._sync_status_timer()
        thread.start()

    def _on_worker_finished(self, snapshot: McpStatusSnapshot) -> None:
        thread = self._worker_thread
        action = self._worker_action
        was_running = self._worker_was_running
        self._pending_worker_result = (action, was_running, snapshot)
        self._worker = None
        if thread is not None:
            thread.quit()
            return
        self._finish_worker_action(None)

    def _on_worker_thread_finished(self, thread) -> None:
        if thread is not self._worker_thread:
            _forget_mcp_worker(thread)
            return
        self._finish_worker_action(thread)

    def _finish_worker_action(self, thread) -> None:
        result = self._pending_worker_result
        self._worker_thread = None
        self._worker = None
        self._worker_action = None
        self._worker_was_running = False
        self._pending_worker_result = None
        if thread is not None:
            _forget_mcp_worker(thread)
        if result is not None and not self._closing:
            action, was_running, snapshot = result
            self.apply_status(snapshot)
            self._update_stop_on_quit_policy(action, was_running, snapshot)
        self.action_button.setEnabled(True)
        self._sync_status_timer()

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
        _forget_mcp_worker(thread)
        self._worker_thread = None
        self._worker = None
        self._worker_action = None
        self._worker_was_running = False
        self._pending_worker_result = None
        self.action_button.setEnabled(True)
        self._sync_status_timer()

    def _on_app_about_to_quit(self) -> None:
        if not self._stop_on_app_quit:
            return
        self._wait_for_worker()
        if not self._running:
            self._stop_on_app_quit = False
            return
        try:
            snapshot = self.backend.stop()
        except Exception as exc:
            snapshot = McpStatusSnapshot(running=False, detail="ошибка остановки MCP", error=str(exc))
        self._stop_on_app_quit = False
        self.apply_status(snapshot)

    def closeEvent(self, event) -> None:
        self._closing = True
        self._status_timer.stop()
        self._on_app_about_to_quit()
        app = QtWidgets.QApplication.instance()
        if app is not None:
            try:
                app.aboutToQuit.disconnect(self._on_app_about_to_quit)
            except TypeError:
                pass
        self._wait_for_worker()
        super().closeEvent(event)

    def refresh_status(self) -> None:
        """Запрашивает статус демона в фоновом потоке: синхронный HTTP-запрос
        (таймаут до 5 с) не должен замораживать GUI при переключении на MCP."""
        self._dispatch_action("status")

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
