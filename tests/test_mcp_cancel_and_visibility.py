"""Фиксы MCP: гонка отмены AI-запроса и видимость подключённых клиентов.

Симптом «отмена не отменяет, интерфейс виснет»: cancel мог прийти в демон ДО
регистрации request_id (executor ещё не отправил /ai/completions) — демон
отвечал «не знаю такого» и забывал, а запоздавший запрос создавал задачу и
висел до 30-минутного таймаута. Фикс — tombstone отменённых request_id.

Симптом «не видит claude/gpt, но видит antigravity»: у всех stdio-клиентов
имя было захардкожено «MCP client» (clientInfo из initialize игнорировался),
а карточка GUI опрашивала статус только когда сама считала демона запущенным.
"""

import os
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gemini_translator.mcp.ai_bridge import list_gui_ai_tasks
from gemini_translator.mcp.client_sessions import McpClientSession
from gemini_translator.mcp.daemon import McpDaemon, _HttpError
from gemini_translator.mcp.server import McpStdioServer


class CancelTombstoneTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.daemon = McpDaemon(self._tmp.name)

    def test_cancel_before_request_kills_late_request(self):
        """Гонка: отмена пришла раньше самого запроса."""
        result = self.daemon.cancel_ai_completion_payload("req_race_1")
        self.assertTrue(result["ok"])

        with self.assertRaises(_HttpError) as caught:
            self.daemon.request_ai_completion(
                {"prompt": "переведи", "request_id": "req_race_1", "timeout_sec": 5}
            )
        self.assertEqual(caught.exception.status, 499)
        # Задача-сирота НЕ создана — внешний ИИ не начнёт отменённую работу
        self.assertEqual(list_gui_ai_tasks(Path(self._tmp.name)), [])

    def test_tombstone_is_consumed_and_expires(self):
        self.daemon.cancel_ai_completion_payload("req_race_2")
        with self.assertRaises(_HttpError) as caught:
            self.daemon.request_ai_completion(
                {"prompt": "p", "request_id": "req_race_2", "timeout_sec": 5}
            )
        self.assertEqual(caught.exception.status, 499)
        # Tombstone одноразовый: повторный запрос с тем же id идёт обычным
        # путём (упрётся в 409 «нет клиентов», а не в 499)
        with self.assertRaises(_HttpError) as caught2:
            self.daemon.request_ai_completion(
                {"prompt": "p", "request_id": "req_race_2", "timeout_sec": 5}
            )
        self.assertNotEqual(caught2.exception.status, 499)

    def test_cancel_of_active_request_still_works(self):
        """Обычный путь: событие активного запроса выставляется."""
        event = threading.Event()
        with self.daemon._lock:
            self.daemon._active_ai_request_cancellations["req_live"] = event
        result = self.daemon.cancel_ai_completion_payload("req_live")
        self.assertTrue(result["cancelled"])
        self.assertTrue(event.is_set())


class ClientNameFromInitializeTests(unittest.TestCase):
    def test_initialize_sets_client_name_from_client_info(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = McpClientSession(Path(tmp), client_name="MCP client")
            try:
                server = McpStdioServer(client_factory=lambda: None, client_session=session)
                server.handle_request({
                    "jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {
                        "clientInfo": {"name": "claude-ai", "version": "1.0"},
                        "capabilities": {"roots": {}},
                    },
                })
                self.assertEqual(session.client_name, "claude-ai")
                on_disk = session.path.read_text(encoding="utf-8")
                self.assertIn("claude-ai", on_disk)
            finally:
                session.close()


class BoundedExecutorShutdownTests(unittest.TestCase):
    def test_returns_before_stuck_thread_finishes(self):
        from gemini_translator.core.translation_engine import shutdown_executor_with_deadline

        release = threading.Event()
        started = threading.Event()
        executor = ThreadPoolExecutor(max_workers=1)
        executor.submit(lambda: (started.set(), release.wait(30)))
        self.assertTrue(started.wait(2))

        t0 = time.monotonic()
        lingering = shutdown_executor_with_deadline(executor, deadline_seconds=0.3)
        elapsed = time.monotonic() - t0

        self.assertLess(elapsed, 2.0)      # не ждали зависший поток
        self.assertGreaterEqual(lingering, 1)
        release.set()


class WidgetPollsWhenIdleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6 import QtWidgets
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _make_card(self):
        from gemini_translator.ui.widgets.mcp_control_widget import (
            McpControlWidget, McpStatusSnapshot,
        )

        class _FakeBackend:
            def status(self):
                return McpStatusSnapshot(running=True)

        card = McpControlWidget(backend=_FakeBackend())
        card.set_auto_refresh_enabled(True)
        self.addCleanup(card.deleteLater)
        return card

    def test_idle_poll_refreshes_only_when_daemon_info_present(self):
        card = self._make_card()
        card._running = False
        calls = []
        card.refresh_status = lambda: calls.append(1)

        card._daemon_info_present = lambda: False
        card._poll_status()
        self.assertEqual(calls, [])

        card._daemon_info_present = lambda: True
        card._poll_status()
        self.assertEqual(calls, [1])

    def test_status_timer_active_even_when_not_running(self):
        card = self._make_card()
        from gemini_translator.ui.widgets.mcp_control_widget import McpStatusSnapshot
        card.apply_status(McpStatusSnapshot(running=False))
        self.assertTrue(card._status_timer.isActive())


if __name__ == "__main__":
    unittest.main()
