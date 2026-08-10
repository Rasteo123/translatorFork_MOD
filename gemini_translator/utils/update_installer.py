# -*- coding: utf-8 -*-
"""Установочный слой апдейтера: staging, ack-протокол, платформенные хелперы.

Модуль сознательно не импортирует Qt: вся логика тестируется без GUI, а
HomePage лишь вызывает её из рабочих потоков. Замена файлов выполняется
отсоединёнными хелперами (PowerShell на Windows, bash на macOS, python для
source-архивов) по транзакционному протоколу:

1. хелпер ждёт завершения родительского PID (ограниченно, не sleep'ом);
2. до любых изменений снимается резервная копия / журнал;
3. новый процесс запускается с одноразовым health-токеном (ACK_ENV);
4. подтверждение — чистка; выход без подтверждения — откат; живой процесс
   без подтверждения — оставить всё как есть и записать HEALTH-TIMEOUT
   (живой exe на Windows заблокирован, а медленный первый запуск может
   ещё стать здоровым — восстанавливать поверх нельзя).
"""
import os
import re
import sys
import json
import time
import shutil
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from gemini_translator.version import __version__

ACK_ENV = "GT_UPDATE_ACK_FILE"
HEALTH_WINDOW_S = 90
APP_EXIT_WAIT_S = 120


class UpdateInstallError(Exception):
    """Ошибка подготовки установки, пригодная для показа пользователю."""

    @property
    def user_message(self) -> str:
        return str(self)


# --- Пути -----------------------------------------------------------------

def staging_root(platform=None, env=None) -> Path:
    """Каталог апдейтера в данных приложения (не создаёт его)."""
    platform = platform or sys.platform
    env = os.environ if env is None else env
    home = Path(env.get("HOME") or os.path.expanduser("~"))
    if platform == "win32":
        base = Path(env.get("LOCALAPPDATA") or (home / "AppData" / "Local"))
    elif platform == "darwin":
        base = home / "Library" / "Application Support"
    else:
        base = Path(env.get("XDG_DATA_HOME") or (home / ".local" / "share"))
    return base / "GeminiTranslator" / "updater"


def update_log_path() -> Path:
    return staging_root() / "updater.log"


def log_update_event(message: str) -> None:
    """Строка в журнал апдейтера; сбой журнала никогда не валит апдейт."""
    try:
        root = staging_root()
        root.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        with open(root / "updater.log", "a", encoding="utf-8") as f:
            f.write(f"[{stamp}] [UPD] {message}\n")
    except Exception:
        pass


# --- Health-протокол ------------------------------------------------------

def write_startup_acknowledgement(env=None) -> None:
    """Пишет ack-файл, если процесс запущен хелпером обновления.

    Вызывается сразу после создания QApplication. Никогда не роняет старт.
    """
    env = os.environ if env is None else env
    try:
        ack_path = env.get(ACK_ENV)
        if not ack_path:
            return
        path = Path(ack_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"pid": os.getpid(), "version": __version__}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        try:
            del env[ACK_ENV]
        except (KeyError, TypeError):
            pass
    except Exception:
        pass


def cleanup_stale_staging(max_age_days: int = 7, root=None) -> None:
    """Убирает забытые .part/бэкапы старше max_age_days; лог не трогает."""
    try:
        root = Path(root) if root is not None else staging_root()
        if not root.is_dir():
            return
        cutoff = time.time() - max_age_days * 86400
        for entry in root.iterdir():
            if entry.name == "updater.log":
                continue
            try:
                if entry.stat().st_mtime >= cutoff:
                    continue
                if entry.is_dir():
                    shutil.rmtree(entry, ignore_errors=True)
                else:
                    entry.unlink()
            except OSError:
                continue
    except Exception:
        pass


# --- Исполняемый файл и окружение -----------------------------------------

def get_real_executable() -> str:
    """Реальный путь к exe приложения на диске.

    В onefile-сборках PyInstaller sys.executable может указывать во
    временный каталог _MEI*, который исчезает при выходе — для рестарта
    нужен путь, по которому пользователь запускал программу.
    """
    if getattr(sys, "frozen", False):
        exe = os.path.abspath(sys.executable)
        if sys.platform == "win32" and re.search(r"_MEI\d+", exe):
            exe = os.path.abspath(sys.argv[0])
        return exe
    return os.path.abspath(sys.executable)


def clean_helper_env() -> dict:
    """Окружение для хелпера/нового процесса без следов PyInstaller."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("_PYI_")}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def launch_detached_helper(argv, *, cwd) -> subprocess.Popen:
    """Запускает отсоединённый хелпер и проверяет, что процесс создан."""
    kwargs = {"cwd": str(cwd), "env": clean_helper_env()}
    if sys.platform == "win32":
        flags = 0
        for name in ("CREATE_NO_WINDOW", "CREATE_NEW_PROCESS_GROUP", "DETACHED_PROCESS"):
            flags |= getattr(subprocess, name, 0)
        kwargs["creationflags"] = flags
    else:
        kwargs["start_new_session"] = True
    try:
        popen = subprocess.Popen(argv, **kwargs)
    except OSError as e:
        raise UpdateInstallError(f"Не удалось запустить хелпер обновления: {e}") from e
    if popen.poll() is not None:
        raise UpdateInstallError(
            f"Хелпер обновления завершился сразу после запуска (код {popen.returncode})")
    return popen


# --- Квотирование ---------------------------------------------------------

def ps_quote(s: str) -> str:
    """Литерал PowerShell в одинарных кавычках (single quote удваивается)."""
    return "'" + str(s).replace("'", "''") + "'"


def sh_quote(s: str) -> str:
    return shlex.quote(str(s))
