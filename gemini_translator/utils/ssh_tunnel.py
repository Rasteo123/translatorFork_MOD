"""Manages a local `ssh -D` SOCKS tunnel as a supervised subprocess."""

from __future__ import annotations

import os
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
    """Return one complete stderr line without blocking the Qt event loop."""
    stderr = getattr(process, "stderr", None)
    if stderr is None:
        return None

    buffer = getattr(process, "_ssh_tunnel_stderr_buffer", b"")
    try:
        fd = stderr.fileno()
        os.set_blocking(fd, False)
        chunk = os.read(fd, 4096)
    except (BlockingIOError, OSError):
        return None

    if not chunk:
        if not buffer:
            return None
        setattr(process, "_ssh_tunnel_stderr_buffer", b"")
        return buffer.decode("utf-8", errors="replace").strip()

    buffer += chunk
    if b"\n" not in buffer:
        setattr(process, "_ssh_tunnel_stderr_buffer", buffer)
        return None

    line, buffer = buffer.split(b"\n", 1)
    setattr(process, "_ssh_tunnel_stderr_buffer", buffer)
    return line.decode("utf-8", errors="replace").strip()


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
        if popen_factory is not None:
            self._popen_factory = popen_factory
        else:
            self._popen_factory = lambda args: subprocess.Popen(args, stderr=subprocess.PIPE)
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

    def start(self, *, ssh_host: str, ssh_port: int, ssh_user: str, ssh_key_path: str, local_port: int) -> None:
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
        if process is None:
            return
        try:
            if process.poll() is not None:
                self._process = None
                return
            process.terminate()
            process.wait(timeout=3)
        except Exception:
            try:
                process.kill()
                process.wait(timeout=3)
            except Exception:
                return
        self._process = None

    def _build_command(self) -> list:
        p = self._params
        return [
            "ssh",
            "-i", str(p["ssh_key_path"]),
            "-p", str(p["ssh_port"]),
            "-D", f"127.0.0.1:{p['local_port']}",
            "-N",
            *_SSH_OPTIONS,
            f"{p['ssh_user']}@{p['ssh_host']}",
        ]

    def _spawn(self) -> None:
        if self._stopped:
            return
        self._last_error = ""
        self.status_changed.emit("connecting", "")
        try:
            self._process = self._popen_factory(self._build_command())
        except Exception as exc:
            self._process = None
            self._last_error = str(exc)
            self.status_changed.emit("error", str(exc))
            self._schedule_restart()
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
