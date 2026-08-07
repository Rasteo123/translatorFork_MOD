import os
import subprocess
import sys
import threading
import time
import textwrap
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from gemini_translator.mcp.client import DaemonClientError
from gemini_translator.ui.widgets.mcp_control_widget import (
    McpControlBackend,
    McpControlWidget,
    McpStatusSnapshot,
)


class _FakeBackend:
    def status(self):
        return McpStatusSnapshot(running=False, detail="stdio + local daemon")

    def start(self):
        return McpStatusSnapshot(running=True, detail="127.0.0.1:12345")

    def stop(self):
        return McpStatusSnapshot(running=False, detail="stdio + local daemon")

    def codex_config(self):
        return "[mcp_servers.translatorFork]\ncommand = \"python\"\n"


class _ActionBackend(_FakeBackend):
    def __init__(self):
        self.started = 0
        self.stopped = 0

    def start(self):
        self.started += 1
        return McpStatusSnapshot(running=True, detail="127.0.0.1:4567")

    def stop(self):
        self.stopped += 1
        return McpStatusSnapshot(running=False, detail="stdio + local daemon")

    def codex_config(self):
        return "[mcp_servers.translatorFork]\ncommand = \"python\"\n"


class _ConfigErrorBackend(_ActionBackend):
    def codex_config(self):
        raise RuntimeError("config boom")


class _BlockingActionBackend(_ActionBackend):
    def __init__(self):
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def start(self):
        self.started += 1
        self.entered.set()
        if not self.release.wait(2):
            raise RuntimeError("timed out waiting for release")
        return McpStatusSnapshot(running=True, detail="127.0.0.1:4567")


class _SlowStatusBackend(_FakeBackend):
    def __init__(self):
        self.status_calls = 0
        self.status_thread = None

    def status(self):
        self.status_calls += 1
        self.status_thread = threading.current_thread()
        time.sleep(0.25)
        return McpStatusSnapshot(running=True, detail="127.0.0.1:9999")


class _FakeThread:
    def __init__(self):
        self.quit_called = False

    def quit(self):
        self.quit_called = True

    def isRunning(self):
        return False


class McpControlWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _process_events_until(self, condition, timeout=1.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if condition():
                return True
            time.sleep(0.01)
        self.app.processEvents()
        return condition()

    def test_initial_state_is_compact_and_off(self):
        widget = McpControlWidget(backend=_FakeBackend())
        self.addCleanup(widget.close)

        self.assertEqual(widget.objectName(), "mcpControlCard")
        self.assertEqual(widget.status_value_label.text(), "Выключен")
        self.assertEqual(widget.detail_label.text(), "stdio + local daemon")
        self.assertEqual(widget.action_button.text(), "Запустить")
        self.assertEqual(widget.config_button.text(), "Codex config")
        self.assertLessEqual(widget.sizeHint().height(), 76)

    def test_initial_state_is_narrow_enough_for_provider_row(self):
        widget = McpControlWidget(backend=_FakeBackend())
        self.addCleanup(widget.close)

        self.assertLessEqual(widget.sizeHint().width(), 320)

    def test_action_button_stylesheet_has_single_closing_brace(self):
        widget = McpControlWidget(backend=_FakeBackend())
        self.addCleanup(widget.close)

        self.assertNotIn("}}", widget.action_button.styleSheet())

    def test_apply_running_status_updates_labels(self):
        widget = McpControlWidget(backend=_FakeBackend())
        self.addCleanup(widget.close)

        widget.apply_status(McpStatusSnapshot(running=True, detail="127.0.0.1:5000"))

        self.assertEqual(widget.status_value_label.text(), "Запущен")
        self.assertEqual(widget.detail_label.text(), "127.0.0.1:5000")
        self.assertEqual(widget.action_button.text(), "Остановить")

    def test_auto_refresh_timer_runs_only_when_enabled_and_daemon_running(self):
        widget = McpControlWidget(backend=_FakeBackend())
        self.addCleanup(widget.close)

        widget.apply_status(McpStatusSnapshot(running=True, detail="127.0.0.1:5000"))
        self.assertFalse(widget._status_timer.isActive())

        widget.set_auto_refresh_enabled(True)
        self.assertTrue(widget._status_timer.isActive())

        widget.apply_status(McpStatusSnapshot(running=False))
        self.assertFalse(widget._status_timer.isActive())

    def test_apply_error_status_keeps_card_usable(self):
        widget = McpControlWidget(backend=_FakeBackend())
        self.addCleanup(widget.close)

        widget.apply_status(McpStatusSnapshot(running=False, detail="ошибка запуска", error="boom"))

        self.assertEqual(widget.status_value_label.text(), "Ошибка")
        self.assertEqual(widget.detail_label.text(), "ошибка запуска")
        self.assertEqual(widget.action_button.text(), "Запустить")
        self.assertIn("boom", widget.toolTip())

    def test_backend_status_treats_stopped_daemon_as_off(self):
        backend = McpControlBackend()

        with mock.patch(
            "gemini_translator.mcp.client.load_client",
            side_effect=DaemonClientError("daemon is not running"),
        ):
            snapshot = backend.status()

        self.assertFalse(snapshot.running)
        self.assertEqual(snapshot.detail, "stdio + local daemon")
        self.assertIsNone(snapshot.error)

    def test_backend_status_treats_unreachable_stale_daemon_as_off(self):
        backend = McpControlBackend()

        class Client:
            def status(self):
                raise DaemonClientError("<urlopen error [Errno 61] Connection refused>")

        with mock.patch("gemini_translator.mcp.client.load_client", return_value=Client()):
            snapshot = backend.status()

        self.assertFalse(snapshot.running)
        self.assertEqual(snapshot.detail, "stdio + local daemon")
        self.assertIsNone(snapshot.error)

    def test_backend_stop_treats_unreachable_stale_daemon_as_off(self):
        backend = McpControlBackend()

        class Client:
            def shutdown(self):
                raise DaemonClientError("<urlopen error [Errno 61] Connection refused>")

        with mock.patch("gemini_translator.mcp.client.load_client", return_value=Client()):
            snapshot = backend.stop()

        self.assertFalse(snapshot.running)
        self.assertEqual(snapshot.detail, "stdio + local daemon")
        self.assertIsNone(snapshot.error)

    def test_backend_status_reads_connected_mcp_client_count(self):
        backend = McpControlBackend()

        class Client:
            def status(self):
                return {
                    "ok": True,
                    "daemon": {"host": "127.0.0.1", "port": 6543},
                    "mcp_clients": {"connected": 1, "items": [{"client_name": "Gemini"}]},
                }

        with mock.patch("gemini_translator.mcp.client.load_client", return_value=Client()):
            snapshot = backend.status()

        self.assertTrue(snapshot.running)
        self.assertEqual(snapshot.detail, "127.0.0.1:6543")
        self.assertEqual(snapshot.connected_clients, 1)

    def test_execute_action_sync_runs_start_then_stop(self):
        backend = _ActionBackend()
        widget = McpControlWidget(backend=backend)
        self.addCleanup(widget.close)

        self.app.processEvents()

        widget._execute_action_sync("toggle")
        self.assertEqual(backend.started, 1)
        self.assertEqual(widget.status_value_label.text(), "Запущен")
        self.assertEqual(widget.action_button.text(), "Остановить")

        widget._execute_action_sync("toggle")
        self.assertEqual(backend.stopped, 1)
        self.assertEqual(widget.status_value_label.text(), "Выключен")
        self.assertEqual(widget.action_button.text(), "Запустить")

    def test_app_quit_stops_daemon_started_by_this_widget(self):
        backend = _ActionBackend()
        widget = McpControlWidget(backend=backend)
        self.addCleanup(widget.close)

        widget._execute_action_sync("toggle")
        widget._on_app_about_to_quit()

        self.assertEqual(backend.started, 1)
        self.assertEqual(backend.stopped, 1)
        self.assertFalse(widget._stop_on_app_quit)

    def test_app_quit_does_not_stop_daemon_discovered_by_status(self):
        backend = _ActionBackend()
        widget = McpControlWidget(backend=backend)
        self.addCleanup(widget.close)

        widget.apply_status(McpStatusSnapshot(running=True, detail="127.0.0.1:4567"))
        widget._on_app_about_to_quit()

        self.assertEqual(backend.stopped, 0)

    def test_close_stops_daemon_started_by_this_widget(self):
        backend = _ActionBackend()
        widget = McpControlWidget(backend=backend)

        widget._execute_action_sync("toggle")
        widget.close()

        self.assertEqual(backend.started, 1)
        self.assertEqual(backend.stopped, 1)

    def test_worker_finished_reenables_button_and_applies_status(self):
        widget = McpControlWidget(backend=_FakeBackend())
        self.addCleanup(widget.close)
        widget.action_button.setEnabled(False)

        widget._on_worker_finished(McpStatusSnapshot(running=True, detail="127.0.0.1:4567"))

        self.assertTrue(widget.action_button.isEnabled())
        self.assertEqual(widget.status_value_label.text(), "Запущен")
        self.assertEqual(widget.detail_label.text(), "127.0.0.1:4567")

    def test_worker_finished_keeps_thread_active_until_qthread_finished(self):
        widget = McpControlWidget(backend=_FakeBackend())
        self.addCleanup(widget.close)
        thread = _FakeThread()
        widget._worker_thread = thread
        widget._worker_action = "toggle"
        widget._worker_was_running = False
        widget.action_button.setEnabled(False)

        widget._on_worker_finished(McpStatusSnapshot(running=True, detail="127.0.0.1:4567"))

        self.assertTrue(thread.quit_called)
        self.assertIs(widget._worker_thread, thread)
        self.assertFalse(widget.action_button.isEnabled())

    def test_refresh_status_does_not_block_gui_thread(self):
        backend = _SlowStatusBackend()
        widget = McpControlWidget(backend=backend)
        self.addCleanup(widget.close)

        started = time.monotonic()
        widget.refresh_status()
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 0.15)
        self.assertTrue(self._process_events_until(
            lambda: widget._worker_thread is None and backend.status_calls == 1,
            timeout=2.0))
        self.assertIsNotNone(backend.status_thread)
        self.assertIsNot(backend.status_thread, threading.main_thread())
        self.assertEqual(widget.status_value_label.text(), "Запущен")

    def test_refresh_status_keeps_action_button_enabled(self):
        backend = _SlowStatusBackend()
        widget = McpControlWidget(backend=backend)
        self.addCleanup(widget.close)

        widget.refresh_status()

        self.assertTrue(widget.action_button.isEnabled())
        self.assertTrue(self._process_events_until(
            lambda: widget._worker_thread is None, timeout=2.0))

    def test_action_button_dispatches_toggle_without_sync_backend_call(self):
        backend = _ActionBackend()
        widget = McpControlWidget(backend=backend)
        self.addCleanup(widget.close)
        dispatched = []
        widget._dispatch_action = dispatched.append

        widget.action_button.click()

        self.assertEqual(dispatched, ["toggle"])
        self.assertEqual(backend.started, 0)

    def test_duplicate_dispatch_is_ignored_while_worker_is_active(self):
        backend = _BlockingActionBackend()
        widget = McpControlWidget(backend=backend)
        self.addCleanup(widget.close)

        widget.action_button.click()
        self.assertTrue(backend.entered.wait(1))
        thread = widget._worker_thread
        self.assertIsNotNone(thread)

        try:
            widget._dispatch_action("toggle")

            self.assertEqual(backend.started, 1)
        finally:
            backend.release.set()
            if thread is not None and thread.isRunning():
                thread.quit()
                self.assertTrue(thread.wait(1000))
            self.assertTrue(self._process_events_until(lambda: widget._worker_thread is None))

    def test_close_waits_for_active_worker_thread(self):
        backend = _BlockingActionBackend()
        widget = McpControlWidget(backend=backend)
        self.addCleanup(widget.close)

        widget.action_button.click()
        self.assertTrue(backend.entered.wait(1))
        thread = widget._worker_thread
        self.assertIsNotNone(thread)
        self.assertTrue(thread.isRunning())
        release_timer = threading.Timer(0.05, backend.release.set)
        release_timer.start()

        try:
            widget.close()

            self.assertIsNone(widget._worker_thread)
            self.assertIsNone(widget._worker)
            self.assertFalse(thread.isRunning())
        finally:
            backend.release.set()
            release_timer.cancel()
            if thread is not None and thread.isRunning():
                thread.quit()
                self.assertTrue(thread.wait(1000))
            self.app.processEvents()

    def test_parent_deletion_keeps_active_worker_thread_alive_until_finished(self):
        script = textwrap.dedent(
            """
            import threading
            import time
            from PyQt6 import QtCore, QtWidgets
            from gemini_translator.ui.widgets.mcp_control_widget import McpControlWidget, McpStatusSnapshot

            class Backend:
                def __init__(self):
                    self.entered = threading.Event()
                    self.release = threading.Event()

                def status(self):
                    return McpStatusSnapshot(False)

                def start(self):
                    self.entered.set()
                    self.release.wait(1)
                    return McpStatusSnapshot(True, "done")

                def stop(self):
                    return McpStatusSnapshot(False)

                def codex_config(self):
                    return "x"

            app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
            parent = QtWidgets.QWidget()
            layout = QtWidgets.QVBoxLayout(parent)
            backend = Backend()
            widget = McpControlWidget(parent, backend=backend)
            layout.addWidget(widget)
            widget.action_button.click()
            assert backend.entered.wait(1)
            thread = widget._worker_thread
            assert thread is not None and thread.isRunning()
            threading.Timer(0.05, backend.release.set).start()
            parent.deleteLater()
            QtWidgets.QApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
            for _ in range(200):
                app.processEvents()
                time.sleep(0.005)
                if not thread.isRunning():
                    break
            print("thread_running_after_delete", thread.isRunning())
            raise SystemExit(1 if thread.isRunning() else 0)
            """
        )
        env = os.environ.copy()
        env["QT_QPA_PLATFORM"] = "offscreen"
        env["QT_FATAL_WARNINGS"] = "1"

        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=os.getcwd(),
            env=env,
            text=True,
            capture_output=True,
            timeout=5,
        )

        self.assertEqual(
            result.returncode,
            0,
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

    def test_copy_codex_config_uses_clipboard(self):
        backend = _ActionBackend()
        widget = McpControlWidget(backend=backend)
        self.addCleanup(widget.close)

        copied = widget.copy_codex_config()

        self.assertTrue(copied.startswith("[mcp_servers.translatorFork]"))
        self.assertEqual(QtWidgets.QApplication.clipboard().text(), copied)

    def test_copy_codex_config_failure_preserves_running_state(self):
        widget = McpControlWidget(backend=_ConfigErrorBackend())
        self.addCleanup(widget.close)
        widget.apply_status(McpStatusSnapshot(running=True, detail="127.0.0.1:4567"))

        copied = widget.copy_codex_config()

        self.assertEqual(copied, "")
        self.assertTrue(widget._running)
        self.assertEqual(widget.status_value_label.text(), "Запущен")
        self.assertEqual(widget.action_button.text(), "Остановить")
        self.assertEqual(widget.detail_label.text(), "ошибка config")
        self.assertIn("config boom", widget.toolTip())


if __name__ == "__main__":
    unittest.main()
