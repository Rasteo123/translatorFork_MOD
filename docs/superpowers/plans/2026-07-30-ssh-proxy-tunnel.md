# SSH Proxy Tunnel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When the user enables "Включить прокси" with tunnel mode set to SSH, the app itself spawns, monitors, and auto-restarts a local `ssh -D` SOCKS tunnel — no manual terminal command needed.

**Architecture:** A small `SshTunnelManager` (QObject, modeled on the existing `PowerInhibitor`) wraps a `ssh -D` subprocess: it spawns it, polls it on a 1s `QTimer`, and restarts with exponential backoff on failure. `GlobalProxyController` (already the single place that reacts to proxy settings events) starts/stops this manager in response to `proxy_settings_changed`/`proxy_started` events, and exposes its status back to the UI over the existing `current_proxy_status` event. `ProxySettingsDialog` gains a tunnel-mode toggle and SSH fields to configure it.

**Tech Stack:** Python 3.11, PyQt6, `subprocess`, `unittest` (existing test style in this repo).

## Global Constraints

- macOS only for this feature (no Windows-specific tunnel code path).
- SSH key has no passphrase — no ssh-agent/passphrase-prompt handling.
- Host key verification is strict: `StrictHostKeyChecking=yes` against the user's own `~/.ssh/known_hosts`, no auto-add.
- Reconnection is infinite with exponential backoff: 3s → 6s → 12s → 24s → 60s (capped at 60s), never gives up while enabled.
- The private key file's contents are never read by the app — only its path is stored and passed to `ssh -i`.
- `tunnel_mode` values are exactly `"none"` (default, existing behavior) or `"ssh"`.
- When `tunnel_mode == "ssh"`, the existing `host`/`port` fields in `proxy_settings` mean the local SOCKS bind (`host` fixed to `"127.0.0.1"`, `port` is the local port) — `user`/`pass` are not used in this mode.
- Command args are always passed as a list to `subprocess.Popen` (never a shell string) — no shell injection surface.

---

### Task 1: `SshTunnelManager` — subprocess lifecycle with backoff

**Files:**
- Create: `gemini_translator/utils/ssh_tunnel.py`
- Test: `tests/test_ssh_tunnel.py`

**Interfaces:**
- Produces: `SshTunnelManager` class with:
  - `__init__(self, popen_factory=None, stderr_reader=None, parent=None)`
  - `.start(self, *, ssh_host: str, ssh_port: int, ssh_user: str, ssh_key_path: str, local_port: int) -> None`
  - `.stop(self) -> None`
  - `.active` (bool property)
  - `.status_changed` — `pyqtSignal(str, str)` emitting `(state, message)` where `state` is one of `"connecting"`, `"up"`, `"down"`, `"error"`
  - Module-level `BACKOFF_SCHEDULE_SECONDS = [3, 6, 12, 24, 60]`

- [x] **Step 1: Write the failing tests**

Create `tests/test_ssh_tunnel.py`:

```python
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from gemini_translator.utils.ssh_tunnel import BACKOFF_SCHEDULE_SECONDS, SshTunnelManager


class _FakeProcess:
    def __init__(self):
        self.terminated = False
        self.killed = False
        self.waited = False
        self._returncode = None

    def poll(self):
        return self._returncode

    def set_exited(self, code=1):
        self._returncode = code

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        self.waited = True


class SshTunnelManagerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _make_manager(self, process=None, stderr_lines=None):
        process = process or _FakeProcess()
        calls = []
        stderr_lines = list(stderr_lines or [])

        def popen_factory(args):
            calls.append(args)
            return process

        def stderr_reader(proc):
            return stderr_lines.pop(0) if stderr_lines else None

        manager = SshTunnelManager(popen_factory=popen_factory, stderr_reader=stderr_reader)
        return manager, process, calls

    def test_start_spawns_ssh_with_expected_arguments_and_emits_up(self):
        manager, process, calls = self._make_manager()
        events = []
        manager.status_changed.connect(lambda state, msg: events.append((state, msg)))

        manager.start(
            ssh_host="91.107.201.91",
            ssh_port=22,
            ssh_user="root",
            ssh_key_path="/Users/rasreo/Documents/ssh/_ssh/id_ed25519",
            local_port=8080,
        )

        self.assertEqual(
            calls,
            [[
                "ssh",
                "-i", "/Users/rasreo/Documents/ssh/_ssh/id_ed25519",
                "-p", "22",
                "-D", "8080",
                "-N",
                "-o", "StrictHostKeyChecking=yes",
                "-o", "ServerAliveInterval=15",
                "-o", "ServerAliveCountMax=3",
                "-o", "ExitOnForwardFailure=yes",
                "root@91.107.201.91",
            ]],
        )
        self.assertTrue(manager.active)
        self.assertEqual(events, [("connecting", ""), ("up", "")])

    def test_process_exit_emits_down_and_schedules_first_backoff(self):
        manager, process, _ = self._make_manager(stderr_lines=["Connection refused"])
        events = []
        manager.status_changed.connect(lambda state, msg: events.append((state, msg)))

        manager.start(
            ssh_host="91.107.201.91", ssh_port=22, ssh_user="root",
            ssh_key_path="/key", local_port=8080,
        )
        process.set_exited(255)
        manager._check_process()

        self.assertFalse(manager.active)
        self.assertEqual(events[-1], ("down", "Connection refused"))
        self.assertTrue(manager._restart_timer.isActive())
        self.assertEqual(manager._restart_timer.interval(), BACKOFF_SCHEDULE_SECONDS[0] * 1000)

    def test_repeated_failures_grow_backoff_up_to_cap(self):
        manager, process, _ = self._make_manager()
        manager.start(
            ssh_host="h", ssh_port=22, ssh_user="root", ssh_key_path="/key", local_port=8080,
        )

        observed_intervals = []
        for _ in range(len(BACKOFF_SCHEDULE_SECONDS) + 2):
            process.set_exited(255)
            manager._check_process()
            observed_intervals.append(manager._restart_timer.interval())
            manager._restart_timer.stop()
            manager._spawn()

        expected = [s * 1000 for s in BACKOFF_SCHEDULE_SECONDS] + [
            BACKOFF_SCHEDULE_SECONDS[-1] * 1000,
            BACKOFF_SCHEDULE_SECONDS[-1] * 1000,
        ]
        self.assertEqual(observed_intervals, expected)

    def test_stop_terminates_process_and_stops_timers(self):
        manager, process, _ = self._make_manager()
        manager.start(
            ssh_host="h", ssh_port=22, ssh_user="root", ssh_key_path="/key", local_port=8080,
        )

        manager.stop()

        self.assertTrue(process.terminated)
        self.assertTrue(process.waited)
        self.assertFalse(manager.active)
        self.assertFalse(manager._check_timer.isActive())
        self.assertFalse(manager._restart_timer.isActive())

    def test_missing_ssh_binary_emits_error_and_does_not_schedule_restart(self):
        def popen_factory(args):
            raise FileNotFoundError("ssh not found")

        manager = SshTunnelManager(popen_factory=popen_factory)
        events = []
        manager.status_changed.connect(lambda state, msg: events.append((state, msg)))

        manager.start(
            ssh_host="h", ssh_port=22, ssh_user="root", ssh_key_path="/key", local_port=8080,
        )

        self.assertEqual(events[-1], ("error", "ssh not found"))
        self.assertFalse(manager._restart_timer.isActive())
        self.assertFalse(manager.active)

    def test_start_is_idempotent_when_already_active(self):
        manager, process, calls = self._make_manager()
        manager.start(
            ssh_host="h", ssh_port=22, ssh_user="root", ssh_key_path="/key", local_port=8080,
        )
        manager.start(
            ssh_host="h", ssh_port=22, ssh_user="root", ssh_key_path="/key", local_port=8080,
        )

        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
```

- [x] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_ssh_tunnel.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'gemini_translator.utils.ssh_tunnel'`

- [x] **Step 3: Write the implementation**

Create `gemini_translator/utils/ssh_tunnel.py`:

```python
"""Manages a local `ssh -D` SOCKS tunnel as a supervised subprocess."""

from __future__ import annotations

import select
import subprocess
from typing import Callable, Optional

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

BACKOFF_SCHEDULE_SECONDS = [3, 6, 12, 24, 60]

_SSH_OPTIONS = [
    "-o", "StrictHostKeyChecking=yes",
    "-o", "ServerAliveInterval=15",
    "-o", "ServerAliveCountMax=3",
    "-o", "ExitOnForwardFailure=yes",
]


def _default_stderr_reader(process) -> Optional[str]:
    """Non-blocking read of the next available line from ssh's stderr."""
    stderr = getattr(process, "stderr", None)
    if stderr is None:
        return None
    ready, _, _ = select.select([stderr], [], [], 0)
    if not ready:
        return None
    line = stderr.readline()
    if not line:
        return None
    if isinstance(line, bytes):
        line = line.decode("utf-8", errors="replace")
    return line.strip()


class SshTunnelManager(QObject):
    """Spawns and supervises `ssh -D` as a local SOCKS5 tunnel."""

    status_changed = pyqtSignal(str, str)  # (state, message)

    def __init__(
        self,
        popen_factory: Optional[Callable[[list], object]] = None,
        stderr_reader: Optional[Callable[[object], Optional[str]]] = None,
        parent=None,
    ):
        super().__init__(parent)
        self._popen_factory = popen_factory or subprocess.Popen
        self._stderr_reader = stderr_reader or _default_stderr_reader
        self._process = None
        self._params = None
        self._failure_count = 0
        self._stopped = True
        self._last_error = ""

        self._check_timer = QTimer(self)
        self._check_timer.setInterval(1000)
        self._check_timer.timeout.connect(self._check_process)

        self._restart_timer = QTimer(self)
        self._restart_timer.setSingleShot(True)
        self._restart_timer.timeout.connect(self._spawn)

    @property
    def active(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self, *, ssh_host, ssh_port, ssh_user, ssh_key_path, local_port) -> None:
        if self.active:
            return
        self._params = {
            "ssh_host": ssh_host,
            "ssh_port": ssh_port,
            "ssh_user": ssh_user,
            "ssh_key_path": ssh_key_path,
            "local_port": local_port,
        }
        self._stopped = False
        self._failure_count = 0
        self._last_error = ""
        self._restart_timer.stop()
        self._spawn()

    def stop(self) -> None:
        self._stopped = True
        self._restart_timer.stop()
        self._check_timer.stop()
        process = self._process
        self._process = None
        if process is None:
            return
        try:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
                process.wait(timeout=3)
            except Exception:
                pass
        except Exception:
            pass

    def _build_command(self) -> list:
        p = self._params
        return [
            "ssh",
            "-i", str(p["ssh_key_path"]),
            "-p", str(p["ssh_port"]),
            "-D", str(p["local_port"]),
            "-N",
            *_SSH_OPTIONS,
            f"{p['ssh_user']}@{p['ssh_host']}",
        ]

    def _spawn(self) -> None:
        if self._stopped:
            return
        self.status_changed.emit("connecting", "")
        try:
            self._process = self._popen_factory(self._build_command())
        except Exception as exc:
            self._process = None
            self._last_error = str(exc)
            self.status_changed.emit("error", str(exc))
            return
        self.status_changed.emit("up", "")
        self._check_timer.start()

    def _check_process(self) -> None:
        if self._stopped or self._process is None:
            return

        while True:
            line = self._stderr_reader(self._process)
            if line is None:
                break
            if line:
                self._last_error = line

        if self._process.poll() is None:
            return

        self._check_timer.stop()
        message = self._last_error or "ssh завершился неожиданно"
        self.status_changed.emit("down", message)
        self._schedule_restart()

    def _schedule_restart(self) -> None:
        if self._stopped:
            return
        index = min(self._failure_count, len(BACKOFF_SCHEDULE_SECONDS) - 1)
        delay = BACKOFF_SCHEDULE_SECONDS[index]
        self._failure_count += 1
        self._restart_timer.start(delay * 1000)
```

- [x] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_ssh_tunnel.py -v`
Expected: PASS (6 tests)

- [x] **Step 5: Commit**

```bash
git add gemini_translator/utils/ssh_tunnel.py tests/test_ssh_tunnel.py
git commit -m "feat: add SshTunnelManager to supervise an ssh -D SOCKS tunnel"
```

---

### Task 2: Wire `GlobalProxyController` to start/stop the tunnel

**Files:**
- Modify: `gemini_translator/utils/proxy_tool.py`
- Test: `tests/test_global_proxy_controller.py`

**Interfaces:**
- Consumes: `SshTunnelManager` from Task 1 — `.start(**kwargs)`, `.stop()`, `.active`, `.status_changed(str, str)`.
- Produces: `GlobalProxyController.__init__(self, event_bus, tunnel_manager=None)` (defaults to a real `SshTunnelManager()` when not passed — the `tunnel_manager` param exists so tests can inject a fake). Adds `GlobalProxyController.shutdown(self) -> None`, called by app shutdown code in Task 3.

- [x] **Step 1: Write the failing tests**

Create `tests/test_global_proxy_controller.py`:

```python
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from gemini_translator.utils.proxy_tool import GlobalProxyController


class _FakeEventBus:
    def __init__(self):
        self._subscribers = []

    def connect(self, callback):
        self._subscribers.append(callback)

    class _EventPostedStub:
        def __init__(self, outer):
            self._outer = outer

        def connect(self, callback):
            self._outer._subscribers.append(callback)

        def emit(self, event):
            for callback in list(self._outer._subscribers):
                callback(event)

    def __post_init(self):
        pass


class _EventBusWithSignal:
    """Matches the subset of EventBus used by GlobalProxyController: event_posted.connect/emit."""

    def __init__(self):
        self._subscribers = []
        self.event_posted = self

    def connect(self, callback):
        self._subscribers.append(callback)

    def emit(self, event):
        for callback in list(self._subscribers):
            callback(event)


class _FakeTunnelManager:
    def __init__(self):
        self.start_calls = []
        self.stop_calls = 0
        self._active = False
        self.status_changed = _Signal()

    @property
    def active(self):
        return self._active

    def start(self, **kwargs):
        self.start_calls.append(kwargs)
        self._active = True

    def stop(self):
        self.stop_calls += 1
        self._active = False


class _Signal:
    def __init__(self):
        self._slots = []

    def connect(self, slot):
        self._slots.append(slot)

    def emit(self, *args):
        for slot in self._slots:
            slot(*args)


class GlobalProxyControllerTunnelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _settings(self, **overrides):
        base = {
            "enabled": True,
            "tunnel_mode": "ssh",
            "host": "127.0.0.1",
            "port": 8080,
            "ssh_host": "91.107.201.91",
            "ssh_port": 22,
            "ssh_user": "root",
            "ssh_key_path": "/Users/rasreo/Documents/ssh/_ssh/id_ed25519",
        }
        base.update(overrides)
        return base

    def test_enabling_ssh_tunnel_mode_starts_the_manager(self):
        bus = _EventBusWithSignal()
        tunnel = _FakeTunnelManager()
        GlobalProxyController(bus, tunnel_manager=tunnel)

        bus.emit({"event": "proxy_settings_changed", "data": self._settings()})

        self.assertEqual(
            tunnel.start_calls,
            [{
                "ssh_host": "91.107.201.91",
                "ssh_port": 22,
                "ssh_user": "root",
                "ssh_key_path": "/Users/rasreo/Documents/ssh/_ssh/id_ed25519",
                "local_port": 8080,
            }],
        )

    def test_disabling_proxy_stops_the_manager(self):
        bus = _EventBusWithSignal()
        tunnel = _FakeTunnelManager()
        GlobalProxyController(bus, tunnel_manager=tunnel)

        bus.emit({"event": "proxy_settings_changed", "data": self._settings()})
        bus.emit({"event": "proxy_settings_changed", "data": self._settings(enabled=False)})

        self.assertEqual(tunnel.stop_calls, 1)

    def test_non_ssh_tunnel_mode_stops_the_manager(self):
        bus = _EventBusWithSignal()
        tunnel = _FakeTunnelManager()
        GlobalProxyController(bus, tunnel_manager=tunnel)

        bus.emit({"event": "proxy_settings_changed", "data": self._settings()})
        bus.emit({"event": "proxy_settings_changed", "data": self._settings(tunnel_mode="none")})

        self.assertEqual(tunnel.stop_calls, 1)

    def test_proxy_started_event_at_app_init_starts_the_manager(self):
        bus = _EventBusWithSignal()
        tunnel = _FakeTunnelManager()
        GlobalProxyController(bus, tunnel_manager=tunnel)

        bus.emit({"event": "proxy_started", "data": self._settings()})

        self.assertEqual(len(tunnel.start_calls), 1)

    def test_already_active_tunnel_is_not_restarted_on_repeat_event(self):
        bus = _EventBusWithSignal()
        tunnel = _FakeTunnelManager()
        GlobalProxyController(bus, tunnel_manager=tunnel)

        bus.emit({"event": "proxy_settings_changed", "data": self._settings()})
        bus.emit({"event": "proxy_settings_changed", "data": self._settings()})

        self.assertEqual(len(tunnel.start_calls), 1)

    def test_tunnel_status_changed_is_relayed_as_current_proxy_status(self):
        bus = _EventBusWithSignal()
        tunnel = _FakeTunnelManager()
        received = []
        bus.connect(lambda event: received.append(event))
        GlobalProxyController(bus, tunnel_manager=tunnel)

        tunnel.status_changed.emit("error", "Permission denied")

        matching = [e for e in received if e.get("event") == "current_proxy_status"]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["data"]["tunnel_state"], "error")
        self.assertEqual(matching[0]["data"]["tunnel_message"], "Permission denied")

    def test_shutdown_stops_an_active_tunnel(self):
        bus = _EventBusWithSignal()
        tunnel = _FakeTunnelManager()
        controller = GlobalProxyController(bus, tunnel_manager=tunnel)

        bus.emit({"event": "proxy_settings_changed", "data": self._settings()})
        controller.shutdown()

        self.assertEqual(tunnel.stop_calls, 1)


if __name__ == "__main__":
    unittest.main()
```

- [x] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_global_proxy_controller.py -v`
Expected: FAIL — `GlobalProxyController.__init__() got an unexpected keyword argument 'tunnel_manager'`

- [x] **Step 3: Write the implementation**

Replace the full contents of `gemini_translator/utils/proxy_tool.py`:

```python
from PyQt6.QtCore import QObject, pyqtSlot

from .ssh_tunnel import SshTunnelManager


class GlobalProxyController(QObject):
    """
    Прокси-контроллер. Транслирует настройки и, для tunnel_mode="ssh",
    сам поднимает и держит локальный SSH-туннель.
    """
    def __init__(self, event_bus, tunnel_manager=None):
        super().__init__()
        self.event_bus = event_bus
        event_bus.event_posted.connect(self.on_event)
        self._tunnel = tunnel_manager if tunnel_manager is not None else SshTunnelManager()
        self._tunnel.status_changed.connect(self._on_tunnel_status_changed)
        print("[PROXY] GlobalProxyController инициализирован.")

    @pyqtSlot(dict)
    def on_event(self, event: dict):
        event_name = event.get('event')
        if event_name == 'proxy_started' or event_name == 'proxy_settings_changed':
            settings = event.get('data', {})
            self._sync_tunnel(settings)
            # Просто уведомляем всех заинтересованных (например, UI)
            self.event_bus.event_posted.emit({
                'event': 'current_proxy_status',
                'source': 'GlobalProxyController',
                'data': settings
            })

    def _sync_tunnel(self, settings: dict) -> None:
        wants_ssh_tunnel = bool(settings.get('enabled')) and settings.get('tunnel_mode') == 'ssh'
        if wants_ssh_tunnel:
            if not self._tunnel.active:
                self._tunnel.start(
                    ssh_host=settings.get('ssh_host'),
                    ssh_port=settings.get('ssh_port'),
                    ssh_user=settings.get('ssh_user'),
                    ssh_key_path=settings.get('ssh_key_path'),
                    local_port=settings.get('port'),
                )
        else:
            if self._tunnel.active:
                self._tunnel.stop()

    def _on_tunnel_status_changed(self, state: str, message: str) -> None:
        self.event_bus.event_posted.emit({
            'event': 'current_proxy_status',
            'source': 'GlobalProxyController',
            'data': {'tunnel_state': state, 'tunnel_message': message},
        })

    def shutdown(self) -> None:
        if self._tunnel.active:
            self._tunnel.stop()
```

- [x] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_global_proxy_controller.py -v`
Expected: PASS (7 tests)

- [x] **Step 5: Run the full existing proxy test suite to check for regressions**

Run: `python -m pytest tests/test_proxy_session_reset.py tests/test_translator_only_proxy_controls.py tests/test_provider_proxy_thinking.py -v`
Expected: PASS (no regressions — these tests don't touch `GlobalProxyController` directly, but confirm nothing else broke)

- [x] **Step 6: Commit**

```bash
git add gemini_translator/utils/proxy_tool.py tests/test_global_proxy_controller.py
git commit -m "feat: auto-start/stop SSH tunnel from GlobalProxyController"
```

---

### Task 3: Stop the tunnel cleanly on app shutdown

**Files:**
- Modify: `main.py:1266-1271`
- Modify: `main_translator_only.py:144-148`

**Interfaces:**
- Consumes: `GlobalProxyController.shutdown()` from Task 2.

- [x] **Step 1: Add shutdown call in `main.py`**

In `main.py`, the shutdown block currently reads (around line 1266):

```python
    # --- ЗАВЕРШЕНИЕ ---
    print(f"[INFO] Приложение завершает работу.")
    if hasattr(app, 'engine_thread') and app.engine_thread.isRunning():
        app.engine_thread.quit()
        app.engine_thread.wait()
    sys.exit(0)
```

Change it to:

```python
    # --- ЗАВЕРШЕНИЕ ---
    print(f"[INFO] Приложение завершает работу.")
    if hasattr(app, 'proxy_controller'):
        app.proxy_controller.shutdown()
    if hasattr(app, 'engine_thread') and app.engine_thread.isRunning():
        app.engine_thread.quit()
        app.engine_thread.wait()
    sys.exit(0)
```

- [x] **Step 2: Add shutdown call in `main_translator_only.py`**

In `main_translator_only.py`, the `finally` block currently reads (around line 144):

```python
    finally:
        print("[INFO] Translator-only application is shutting down.")
        if hasattr(app, "engine_thread") and app.engine_thread.isRunning():
            app.engine_thread.quit()
            app.engine_thread.wait()
```

Change it to:

```python
    finally:
        print("[INFO] Translator-only application is shutting down.")
        if hasattr(app, "proxy_controller"):
            app.proxy_controller.shutdown()
        if hasattr(app, "engine_thread") and app.engine_thread.isRunning():
            app.engine_thread.quit()
            app.engine_thread.wait()
```

- [x] **Step 3: Verify by reading the changed sections back**

Run: `grep -n "proxy_controller.shutdown" main.py main_translator_only.py`
Expected: one match in each file, inside the shutdown blocks shown above.

- [x] **Step 4: Run the full test suite to confirm nothing broke**

Run: `python -m pytest tests/ -x -q`
Expected: PASS (these two files aren't directly unit-tested — this is a smoke check that nothing else regressed)

- [x] **Step 5: Commit**

```bash
git add main.py main_translator_only.py
git commit -m "fix: stop the SSH tunnel process on app shutdown"
```

---

### Task 4: `ProxySettingsDialog` — SSH tunnel mode UI

**Files:**
- Modify: `gemini_translator/ui/dialogs/proxy.py`
- Test: `tests/test_proxy_dialog_ssh_mode.py`

**Interfaces:**
- Consumes: `proxy_settings` fields from Task 1/2's schema: `tunnel_mode`, `ssh_host`, `ssh_port`, `ssh_user`, `ssh_key_path`.
- Produces: `ProxySettingsDialog.validate_inputs(self) -> bool` extended to validate the new fields when `tunnel_mode == "ssh"`; `ProxySettingsDialog.accept(self)` persists them; a `proxy_status_label` on the dialog updated via `current_proxy_status` events carrying `tunnel_state`/`tunnel_message`.

- [x] **Step 1: Write the failing test for validation logic**

Create `tests/test_proxy_dialog_ssh_mode.py`. This uses the same "unbound-method harness" pattern already used in `tests/test_translator_only_proxy_controls.py` — it exercises `validate_inputs`/`_tunnel_mode_selected` against lightweight stub widgets instead of building the full real dialog:

```python
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gemini_translator.ui.dialogs.proxy import ProxySettingsDialog


class _LineEditStub:
    def __init__(self, text=""):
        self._text = text

    def text(self):
        return self._text

    def setText(self, value):
        self._text = value

    def clear(self):
        self._text = ""


class _ComboStub:
    def __init__(self, value="Обычный прокси"):
        self._value = value

    def currentText(self):
        return self._value

    def setCurrentText(self, value):
        self._value = value


class _ValidationHarness:
    validate_inputs = ProxySettingsDialog.validate_inputs
    _is_ssh_mode = ProxySettingsDialog._is_ssh_mode

    def __init__(self, *, tunnel_mode="Обычный прокси", port="8080", host="127.0.0.1",
                 ssh_host="91.107.201.91", ssh_user="root", ssh_key_path="/tmp/does-not-exist"):
        self.tunnel_mode_combo = _ComboStub(tunnel_mode)
        self.proxy_port_edit = _LineEditStub(port)
        self.proxy_host_edit = _LineEditStub(host)
        self.ssh_host_edit = _LineEditStub(ssh_host)
        self.ssh_user_edit = _LineEditStub(ssh_user)
        self.ssh_key_path_edit = _LineEditStub(ssh_key_path)
        self.warnings = []

    def _warn(self, title, message):
        self.warnings.append((title, message))


def _patch_warning(monkeypatch):
    import gemini_translator.ui.dialogs.proxy as proxy_module

    def fake_warning(self, title, message):
        self._warn(title, message)

    monkeypatch.setattr(proxy_module.QMessageBox, "warning", fake_warning)


class ProxyDialogSshValidationTests(unittest.TestCase):
    def setUp(self):
        import pytest
        self._monkeypatch_ctx = pytest.MonkeyPatch()
        _patch_warning(self._monkeypatch_ctx)

    def tearDown(self):
        self._monkeypatch_ctx.undo()

    def test_normal_proxy_mode_ignores_ssh_fields(self):
        harness = _ValidationHarness(tunnel_mode="Обычный прокси", ssh_host="", ssh_user="", ssh_key_path="")

        self.assertTrue(harness.validate_inputs())
        self.assertEqual(harness.warnings, [])

    def test_ssh_mode_requires_existing_key_file(self):
        harness = _ValidationHarness(tunnel_mode="Автотуннель через SSH", ssh_key_path="/tmp/does-not-exist-xyz")

        self.assertFalse(harness.validate_inputs())
        self.assertEqual(len(harness.warnings), 1)

    def test_ssh_mode_requires_ssh_host(self):
        harness = _ValidationHarness(tunnel_mode="Автотуннель через SSH", ssh_host="")

        self.assertFalse(harness.validate_inputs())

    def test_ssh_mode_requires_ssh_user(self):
        harness = _ValidationHarness(tunnel_mode="Автотуннель через SSH", ssh_user="")

        self.assertFalse(harness.validate_inputs())

    def test_ssh_mode_passes_with_valid_existing_key_file(self):
        import tempfile
        with tempfile.NamedTemporaryFile() as tmp_key:
            harness = _ValidationHarness(tunnel_mode="Автотуннель через SSH", ssh_key_path=tmp_key.name)
            self.assertTrue(harness.validate_inputs())
            self.assertEqual(harness.warnings, [])


if __name__ == "__main__":
    unittest.main()
```

- [x] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_proxy_dialog_ssh_mode.py -v`
Expected: FAIL — `AttributeError: type object 'ProxySettingsDialog' has no attribute '_is_ssh_mode'`

- [x] **Step 3: Implement the dialog changes**

In `gemini_translator/ui/dialogs/proxy.py`, add the tunnel-mode combo and SSH fields to `init_ui`. Insert this right after the existing "Тип прокси" block (after line 55, before the "Хост" block):

```python
        # Режим туннеля
        tunnel_mode_layout = QHBoxLayout()
        self.tunnel_mode_label = QLabel("Режим:")
        self.tunnel_mode_combo = QComboBox()
        self.tunnel_mode_combo.addItems(["Обычный прокси", "Автотуннель через SSH"])
        self.tunnel_mode_combo.currentTextChanged.connect(self._on_tunnel_mode_changed)
        tunnel_mode_layout.addWidget(self.tunnel_mode_label)
        tunnel_mode_layout.addWidget(self.tunnel_mode_combo)
        current_proxy_layout.addLayout(tunnel_mode_layout)

        # Поля для режима SSH-туннеля
        self.ssh_host_label = QLabel("SSH-хост:")
        self.ssh_host_edit = QLineEdit()
        current_proxy_layout.addWidget(self.ssh_host_label)
        current_proxy_layout.addWidget(self.ssh_host_edit)

        self.ssh_port_label = QLabel("SSH-порт:")
        self.ssh_port_edit = QLineEdit("22")
        current_proxy_layout.addWidget(self.ssh_port_label)
        current_proxy_layout.addWidget(self.ssh_port_edit)

        self.ssh_user_label = QLabel("SSH-пользователь:")
        self.ssh_user_edit = QLineEdit()
        current_proxy_layout.addWidget(self.ssh_user_label)
        current_proxy_layout.addWidget(self.ssh_user_edit)

        ssh_key_layout = QHBoxLayout()
        self.ssh_key_path_label = QLabel("Путь к приватному ключу:")
        self.ssh_key_path_edit = QLineEdit()
        self.ssh_key_browse_btn = QPushButton("Обзор...")
        self.ssh_key_browse_btn.clicked.connect(self._browse_ssh_key)
        ssh_key_layout.addWidget(self.ssh_key_path_edit)
        ssh_key_layout.addWidget(self.ssh_key_browse_btn)
        current_proxy_layout.addWidget(self.ssh_key_path_label)
        current_proxy_layout.addLayout(ssh_key_layout)

        self.tunnel_status_label = QLabel("Статус: отключено")
        current_proxy_layout.addWidget(self.tunnel_status_label)
```

Add these new methods to the class (near `clear_edit_fields`):

```python
    def _is_ssh_mode(self) -> bool:
        return self.tunnel_mode_combo.currentText() == "Автотуннель через SSH"

    def _on_tunnel_mode_changed(self, _text: str) -> None:
        is_ssh = self._is_ssh_mode()
        for widget in (
            self.ssh_host_label, self.ssh_host_edit,
            self.ssh_port_label, self.ssh_port_edit,
            self.ssh_user_label, self.ssh_user_edit,
            self.ssh_key_path_label, self.ssh_key_path_edit, self.ssh_key_browse_btn,
        ):
            widget.setVisible(is_ssh)
        self.proxy_user_label.setVisible(not is_ssh)
        self.proxy_user_edit.setVisible(not is_ssh)
        self.proxy_pass_label.setVisible(not is_ssh)
        self.proxy_pass_edit.setVisible(not is_ssh)

        # В режиме SSH-туннеля "Хост" всегда локальный (127.0.0.1) — клиент
        # подключается к своему же туннелю, а не к SSH-хосту напрямую.
        if is_ssh:
            self.proxy_host_edit.setText("127.0.0.1")
            self.proxy_host_edit.setEnabled(False)
            self.proxy_host_label.setText("Локальный хост:")
            self.proxy_port_label.setText("Локальный порт туннеля:")
        else:
            self.proxy_host_edit.setEnabled(True)
            self.proxy_host_label.setText("Хост:")
            self.proxy_port_label.setText("Порт:")

    def _browse_ssh_key(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Выберите приватный ключ SSH")
        if path:
            self.ssh_key_path_edit.setText(path)
```

Extend `validate_inputs` (replace the existing method):

```python
    def validate_inputs(self):
        """Проверяет введенные данные."""
        try:
            port = int(self.proxy_port_edit.text())
            if not (1 <= port <= 65535):
                QMessageBox.warning(self, "Warning", "Порт должен быть числом от 1 до 65535.")
                return False
        except ValueError:
            QMessageBox.warning(self, "Warning", "Неверный формат порта.")
            return False
        if not self.proxy_host_edit.text():
            QMessageBox.warning(self, "Warning", "Укажите хост прокси-сервера.")
            return False

        if self._is_ssh_mode():
            if not self.ssh_host_edit.text():
                QMessageBox.warning(self, "Warning", "Укажите SSH-хост.")
                return False
            if not self.ssh_user_edit.text():
                QMessageBox.warning(self, "Warning", "Укажите SSH-пользователя.")
                return False
            key_path = self.ssh_key_path_edit.text()
            if not key_path or not os.path.isfile(key_path):
                QMessageBox.warning(self, "Warning", "Укажите существующий файл приватного ключа.")
                return False

        return True
```

Note: `os` is already imported at the top of other utils modules in this project but not currently in `proxy.py` — add `import os` to the top of `gemini_translator/ui/dialogs/proxy.py` alongside the existing `import json`.

Note on scope: the spec suggested showing the SSH host (instead of `127.0.0.1`) in the saved-proxies table's "Хост" column for SSH-mode entries. Skipping that here — `get_proxy_from_table_row` matches a saved entry by comparing the table's displayed host text against the stored `host` field, and for SSH-mode entries the stored `host` is always `"127.0.0.1"`; displaying `ssh_host` instead would make that match silently fail (breaking populate/delete for those rows). The `host` column keeps showing the real stored value (`127.0.0.1`); the SSH target is visible in the SSH fields once the row is selected via `populate_edit_fields`, which already fills `ssh_host_edit` from Step 3's `populate_edit_fields` update below — no separate table-column change needed.

Update `populate_edit_fields` to also fill the SSH fields when the selected row is an SSH-mode entry (add at the end of the method, after the existing password line):

```python
        if selected_proxy:
            self.proxy_pass_edit.setText(selected_proxy.get('pass', ''))
            is_ssh = selected_proxy.get('tunnel_mode') == 'ssh'
            self.tunnel_mode_combo.setCurrentText(
                "Автотуннель через SSH" if is_ssh else "Обычный прокси"
            )
            self.ssh_host_edit.setText(selected_proxy.get('ssh_host', ''))
            self.ssh_port_edit.setText(str(selected_proxy.get('ssh_port', 22)))
            self.ssh_user_edit.setText(selected_proxy.get('ssh_user', ''))
            self.ssh_key_path_edit.setText(selected_proxy.get('ssh_key_path', ''))
            self._on_tunnel_mode_changed(self.tunnel_mode_combo.currentText())
```

Update `load_settings` to populate the new fields (add at the end of the method, after `self.proxy_enabled_checkbox.setChecked(...)`):

```python
        tunnel_mode = settings.get('tunnel_mode', 'none')
        self.tunnel_mode_combo.setCurrentText(
            "Автотуннель через SSH" if tunnel_mode == "ssh" else "Обычный прокси"
        )
        self.ssh_host_edit.setText(settings.get('ssh_host', ''))
        self.ssh_port_edit.setText(str(settings.get('ssh_port', 22)))
        self.ssh_user_edit.setText(settings.get('ssh_user', ''))
        self.ssh_key_path_edit.setText(settings.get('ssh_key_path', ''))
        self._on_tunnel_mode_changed(self.tunnel_mode_combo.currentText())
```

Update `accept` to persist the new fields — replace the `proxy_settings = {...}` block and the final `save_proxy_settings` call:

```python
        proxy_settings = {
            'enabled': self.proxy_enabled_checkbox.isChecked(),
            'type': self.proxy_type_combo.currentText(),
            'host': self.proxy_host_edit.text(),
            'port': int(self.proxy_port_edit.text()),
            'user': self.proxy_user_edit.text(),
            'pass': self.proxy_pass_edit.text(),
            'tunnel_mode': 'ssh' if self._is_ssh_mode() else 'none',
            'ssh_host': self.ssh_host_edit.text(),
            'ssh_port': int(self.ssh_port_edit.text() or 22),
            'ssh_user': self.ssh_user_edit.text(),
            'ssh_key_path': self.ssh_key_path_edit.text(),
        }
```

(the duplicate-detection loop and `saved_proxies.append(proxy_settings)` below stay as-is), and change the final `self.settings_manager.save_proxy_settings({...})` call to include the same new keys:

```python
        self.settings_manager.save_proxy_settings(
            {
                "enabled": self.proxy_enabled_checkbox.isChecked(),
                "type": self.proxy_type_combo.currentText(),
                "host": self.proxy_host_edit.text(),
                "port": int(self.proxy_port_edit.text()),
                "user": self.proxy_user_edit.text(),
                "pass": self.proxy_pass_edit.text(),
                "tunnel_mode": 'ssh' if self._is_ssh_mode() else 'none',
                "ssh_host": self.ssh_host_edit.text(),
                "ssh_port": int(self.ssh_port_edit.text() or 22),
                "ssh_user": self.ssh_user_edit.text(),
                "ssh_key_path": self.ssh_key_path_edit.text(),
                "saved_proxies": saved_proxies
            }
        )
```

- [x] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_proxy_dialog_ssh_mode.py -v`
Expected: PASS (5 tests)

- [x] **Step 5: Wire the tunnel status label to `current_proxy_status`**

Add an event-bus subscription so the dialog updates `tunnel_status_label` live. Add to `__init__` (after `self.load_settings()`):

```python
        if self.settings_manager.bus is not None:
            self.settings_manager.bus.event_posted.connect(self._on_proxy_event)

    def _on_proxy_event(self, event: dict) -> None:
        if event.get('event') != 'current_proxy_status':
            return
        data = event.get('data', {})
        if 'tunnel_state' not in data:
            return
        state = data['tunnel_state']
        message = data.get('tunnel_message', '')
        labels = {
            'connecting': 'Статус: подключение...',
            'up': 'Статус: активен',
            'down': f'Статус: отключён ({message})' if message else 'Статус: отключён',
            'error': f'Статус: ошибка ({message})' if message else 'Статус: ошибка',
        }
        self.tunnel_status_label.setText(labels.get(state, f'Статус: {state}'))
```

Check `SettingsManager` exposes `.bus` as a plain attribute (already used elsewhere — confirm with `grep -n "self.bus" gemini_translator/utils/settings.py`) before wiring; if the attribute is named differently, use that name instead.

- [x] **Step 6: Run the full existing dialog-adjacent test suite**

Run: `python -m pytest tests/test_proxy_dialog_ssh_mode.py tests/test_setup_tab_chrome.py tests/test_translator_only_proxy_controls.py -v`
Expected: PASS (no regressions in existing proxy/setup dialog tests)

- [x] **Step 7: Commit**

```bash
git add gemini_translator/ui/dialogs/proxy.py tests/test_proxy_dialog_ssh_mode.py
git commit -m "feat: add SSH tunnel mode fields and live status to ProxySettingsDialog"
```

---

### Task 5: Manual end-to-end smoke test (not automated)

**Files:** none (manual verification only — real SSH connectivity to the user's own VPS can't be covered by an automated test).

- [ ] **Step 1: Launch the translator-only app**

Run: `python main_translator_only.py`

- [ ] **Step 2: Open the proxy dialog and configure SSH tunnel mode**

Open Прокси dialog → select "Автотуннель через SSH" → fill in:
- SSH-хост: `91.107.201.91`
- SSH-порт: `22`
- SSH-пользователь: `root`
- Путь к приватному ключу: `/Users/rasreo/Documents/ssh/_ssh/id_ed25519`
- Локальный порт: `8080`
- Check "Включить прокси" → click "Принять"

Expected: status label under the checkbox goes "подключение..." → "активен" within a few seconds; `lsof -nP -iTCP:8080 -sTCP:LISTEN` from a terminal shows an `ssh` process listening.

- [ ] **Step 3: Verify translation actually uses the tunnel**

Start a translation of a small test chapter. Expected: no `ProxyConnectionError` in the log; requests succeed.

- [x] **Step 4: Verify auto-restart on tunnel death** — covered headlessly (see note below)

While translation is running, find and kill the ssh process: `pkill -f "ssh.*91.107.201.91"`. Expected: status label flips to "отключён" then back to "подключение..." within ~3-9 seconds (first backoff step), and `lsof -nP -iTCP:8080 -sTCP:LISTEN` shows a new ssh process with a different PID. Translation should recover on its own retry without user intervention.

- [x] **Step 5: Verify clean shutdown** — covered headlessly at the `stop()` level (see note below)

Close the application normally. Expected: `ps aux | grep "[s]sh.*91.107.201.91"` shows no lingering ssh process afterward.

- [ ] **Step 6: Verify disabling the proxy stops the tunnel**

Relaunch the app, enable the SSH tunnel again, then uncheck "Включить прокси" and click "Принять" without closing the app. Expected: `lsof -nP -iTCP:8080 -sTCP:LISTEN` shows nothing shortly after.

---

#### Headless smoke-test result (2026-08-07)

`SshTunnelManager` was driven directly against the real VPS (no GUI), covering the
mechanics behind Steps 4 and 5. All checks passed:

- `ssh` spawned and listened on `127.0.0.1:8080`; a local SOCKS5 greeting
  (`05 01 00`) got the correct `05 00` reply — no traffic sent through the VPS.
- Status sequence was `connecting → up`.
- Killing the ssh process externally produced `down` ("ssh завершился неожиданно" —
  ssh writes nothing to stderr when killed, so the fallback message is used), then
  `connecting → up` again within the first backoff step, on a **new PID**, with the
  SOCKS5 handshake working again.
- `stop()` freed the port, left `active == False`, and no stray `ssh` process remained.

Still requires a human at the GUI: Steps 1–3 (dialog round-trip and an actual
translation running through the tunnel) and Step 6 (unchecking "Включить прокси"
stops the tunnel — the controller path is unit-tested, but not exercised live).
