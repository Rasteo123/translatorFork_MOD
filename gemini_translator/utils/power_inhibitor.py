"""Small cross-platform guard that keeps translation sessions from sleeping."""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Callable


PREVENT_SLEEP_SETTING_KEY = "prevent_sleep_during_translation"

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002


def load_prevent_sleep_setting(settings_manager, default: bool = False) -> bool:
    loader = getattr(settings_manager, "load_full_session_settings", None)
    if not callable(loader):
        return bool(default)
    try:
        settings = loader() or {}
    except Exception:
        return bool(default)
    if not isinstance(settings, dict):
        return bool(default)
    return bool(settings.get(PREVENT_SLEEP_SETTING_KEY, default))


def save_prevent_sleep_setting(settings_manager, enabled: bool) -> bool:
    loader = getattr(settings_manager, "load_full_session_settings", None)
    saver = getattr(settings_manager, "save_full_session_settings", None)
    if not callable(saver):
        return False
    try:
        settings = loader() if callable(loader) else {}
    except Exception:
        settings = {}
    if not isinstance(settings, dict):
        settings = {}
    settings = dict(settings)
    settings[PREVENT_SLEEP_SETTING_KEY] = bool(enabled)
    try:
        return bool(saver(settings))
    except Exception:
        return False


class PowerInhibitor:
    """Prevent system sleep while still allowing display power saving."""

    def __init__(
        self,
        *,
        platform_name: str | None = None,
        popen_factory: Callable[[list[str]], object] | None = None,
        ctypes_module=None,
    ):
        self.platform_name = platform_name or sys.platform
        self._popen_factory = popen_factory or subprocess.Popen
        self._ctypes_module = ctypes_module
        self._process = None
        self._windows_active = False
        self.last_error = None

    @property
    def active(self) -> bool:
        if self.platform_name == "darwin":
            return self._process is not None and self._process.poll() is None
        if self.platform_name.startswith("win"):
            return self._windows_active
        return False

    def prevent_sleep(self) -> bool:
        if self.active:
            return True

        self.last_error = None
        if self.platform_name == "darwin":
            try:
                # -i prevents idle sleep, -m prevents disk sleep, -s prevents system sleep,
                # -d prevents display sleep. We intentionally block display sleep so that 
                # macOS doesn't go to the lock screen, which stops our background processing.
                self._process = self._popen_factory(
                    ["caffeinate", "-dims", "-w", str(os.getpid())]
                )
                return self.active
            except Exception as exc:
                self.last_error = str(exc)
                self._process = None
                return False

        if self.platform_name.startswith("win"):
            try:
                ctypes_module = self._ctypes_module
                if ctypes_module is None:
                    import ctypes as ctypes_module
                flags = ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
                result = ctypes_module.windll.kernel32.SetThreadExecutionState(flags)
                self._windows_active = bool(result)
                return self._windows_active
            except Exception as exc:
                self.last_error = str(exc)
                self._windows_active = False
                return False

        return False

    def allow_sleep(self) -> None:
        if self.platform_name == "darwin":
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
            return

        if self.platform_name.startswith("win"):
            if not self._windows_active:
                return
            self._windows_active = False
            try:
                ctypes_module = self._ctypes_module
                if ctypes_module is None:
                    import ctypes as ctypes_module
                ctypes_module.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
            except Exception as exc:
                self.last_error = str(exc)

    def __del__(self):
        try:
            self.allow_sleep()
        except Exception:
            pass
