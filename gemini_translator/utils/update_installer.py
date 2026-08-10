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


def _render(template: str, values: dict) -> str:
    """Подставляет @@ИМЯ@@-маркеры; f-string/format не годятся из-за
    фигурных скобок и $ в PowerShell/bash."""
    out = template
    for key, value in values.items():
        out = out.replace(f"@@{key}@@", str(value))
    return out


# --- Контекст установки ---------------------------------------------------

@dataclass(frozen=True)
class InstallContext:
    app_pid: int
    real_executable: str
    version_label: str


def directory_size(path) -> int:
    total = 0
    for base, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(base, name))
            except OSError:
                continue
    return total


# --- Windows: установленная версия (Inno Setup) ---------------------------

_WINDOWS_INSTALLED_TEMPLATE = r"""$ErrorActionPreference = 'Continue'
function Log($m) { Add-Content -LiteralPath @@LOG@@ -Value ("[{0}] [UPD] {1}" -f (Get-Date -Format o), $m) }
function Restore {
  Log 'restoring backup'
  robocopy @@BACKUP@@ @@APPDIR@@ /MIR /R:2 /W:2 | Out-Null
  if ($LASTEXITCODE -ge 8) { Log ('CRITICAL: restore failed; manual recovery from ' + @@BACKUP@@); return $false }
  return $true
}
Log ('waiting for app pid ' + @@PID@@)
Wait-Process -Id @@PID@@ -Timeout @@WAIT@@ -ErrorAction SilentlyContinue
if (Get-Process -Id @@PID@@ -ErrorAction SilentlyContinue) { Log 'app still running; aborting untouched'; exit 1 }
Log 'creating snapshot backup'
robocopy @@APPDIR@@ @@BACKUP@@ /MIR /R:2 /W:2 | Out-Null
if ($LASTEXITCODE -ge 8) { Log 'snapshot failed; aborting untouched'; exit 1 }
Log 'running setup'
$setup = Start-Process -FilePath @@SETUP@@ -ArgumentList '/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART' -Wait -PassThru
if ($setup.ExitCode -ne 0) {
  Log ('setup failed with exit code ' + $setup.ExitCode)
  if (Restore) { Start-Process -FilePath @@EXE@@ }
  exit 1
}
Remove-Item -LiteralPath @@ACK@@ -Force -ErrorAction SilentlyContinue
$env:GT_UPDATE_ACK_FILE = @@ACK@@
$app = Start-Process -FilePath @@EXE@@ -PassThru
$deadline = (Get-Date).AddSeconds(@@HEALTH@@)
while ((Get-Date) -lt $deadline) {
  if (Test-Path -LiteralPath @@ACK@@) {
    Log 'health ack received; cleaning up'
    Remove-Item -Recurse -Force -LiteralPath @@BACKUP@@ -ErrorAction SilentlyContinue
    Remove-Item -Force -LiteralPath @@SETUP@@ -ErrorAction SilentlyContinue
    exit 0
  }
  if ($app.HasExited) {
    Log 'new process exited without ack; rolling back'
    if (Restore) { Start-Process -FilePath @@EXE@@ }
    exit 1
  }
  Start-Sleep -Milliseconds 500
}
Log 'HEALTH-TIMEOUT: process alive without ack; keeping backup and staged installer'
exit 2"""


def render_windows_installed_script(*, app_pid, setup_path, app_dir, backup_dir,
                                    real_exe, ack_path, log_path) -> str:
    return _render(_WINDOWS_INSTALLED_TEMPLATE, {
        "PID": int(app_pid),
        "WAIT": APP_EXIT_WAIT_S,
        "HEALTH": HEALTH_WINDOW_S,
        "LOG": ps_quote(log_path),
        "SETUP": ps_quote(setup_path),
        "APPDIR": ps_quote(app_dir),
        "BACKUP": ps_quote(backup_dir),
        "EXE": ps_quote(real_exe),
        "ACK": ps_quote(ack_path),
    })


# --- Windows: портативная версия ------------------------------------------

_WINDOWS_PORTABLE_TEMPLATE = r"""$ErrorActionPreference = 'Continue'
function Log($m) { Add-Content -LiteralPath @@LOG@@ -Value ("[{0}] [UPD] {1}" -f (Get-Date -Format o), $m) }
Log ('waiting for app pid ' + @@PID@@)
Wait-Process -Id @@PID@@ -Timeout @@WAIT@@ -ErrorAction SilentlyContinue
if (Get-Process -Id @@PID@@ -ErrorAction SilentlyContinue) { Log 'app still running; aborting untouched'; exit 1 }
$bak = @@EXE@@ + '.bak'
Remove-Item -LiteralPath $bak -Force -ErrorAction SilentlyContinue
$moved = $false
foreach ($i in 1..3) {
  try { Move-Item -LiteralPath @@EXE@@ -Destination $bak -Force -ErrorAction Stop; $moved = $true; break }
  catch { Start-Sleep -Seconds 2 }
}
if (-not $moved) { Log 'could not move current exe; aborting untouched'; exit 1 }
try { Move-Item -LiteralPath @@STAGED@@ -Destination @@EXE@@ -Force -ErrorAction Stop }
catch {
  Log 'staged move failed; restoring'
  Move-Item -LiteralPath $bak -Destination @@EXE@@ -Force -ErrorAction SilentlyContinue
  exit 1
}
Remove-Item -LiteralPath @@ACK@@ -Force -ErrorAction SilentlyContinue
$env:GT_UPDATE_ACK_FILE = @@ACK@@
$app = Start-Process -FilePath @@EXE@@ -PassThru
$deadline = (Get-Date).AddSeconds(@@HEALTH@@)
while ((Get-Date) -lt $deadline) {
  if (Test-Path -LiteralPath @@ACK@@) {
    Log 'health ack received; cleaning up'
    Remove-Item -LiteralPath $bak -Force -ErrorAction SilentlyContinue
    exit 0
  }
  if ($app.HasExited) {
    Log 'new process exited without ack; rolling back'
    Move-Item -LiteralPath @@EXE@@ -Destination (@@EXE@@ + '.rejected') -Force -ErrorAction SilentlyContinue
    Move-Item -LiteralPath $bak -Destination @@EXE@@ -Force -ErrorAction SilentlyContinue
    Start-Process -FilePath @@EXE@@
    exit 1
  }
  Start-Sleep -Milliseconds 500
}
Log 'HEALTH-TIMEOUT: process alive without ack; keeping .bak'
exit 2"""


def render_windows_portable_script(*, app_pid, staged_exe, real_exe,
                                   ack_path, log_path) -> str:
    return _render(_WINDOWS_PORTABLE_TEMPLATE, {
        "PID": int(app_pid),
        "WAIT": APP_EXIT_WAIT_S,
        "HEALTH": HEALTH_WINDOW_S,
        "LOG": ps_quote(log_path),
        "STAGED": ps_quote(staged_exe),
        "EXE": ps_quote(real_exe),
        "ACK": ps_quote(ack_path),
    })


def _write_helper_script(name: str, content: str) -> Path:
    root = staging_root()
    root.mkdir(parents=True, exist_ok=True)
    path = root / name
    with open(path, "w", encoding="utf-8-sig") as f:  # BOM: кириллица в PS 5.1
        f.write(content)
    return path


def _powershell_argv(script_path) -> list:
    return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(script_path)]


def prepare_windows_installed(staged_setup, ctx: InstallContext) -> subprocess.Popen:
    """Готовит и запускает хелпер установки Setup.exe с бэкапом каталога."""
    app_dir = Path(ctx.real_executable).parent
    root = staging_root()
    root.mkdir(parents=True, exist_ok=True)
    needed = directory_size(app_dir)
    free = shutil.disk_usage(root).free
    if free < needed:
        raise UpdateInstallError(
            "Недостаточно места на диске для резервной копии перед обновлением: "
            f"нужно ~{needed // (1024 * 1024)} МБ, свободно {free // (1024 * 1024)} МБ")
    backup_dir = root / f"backup-{ctx.version_label}"
    ack_path = root / f"ack-{ctx.version_label}.json"
    try:
        ack_path.unlink()
    except OSError:
        pass
    script = render_windows_installed_script(
        app_pid=ctx.app_pid, setup_path=str(staged_setup), app_dir=str(app_dir),
        backup_dir=str(backup_dir), real_exe=ctx.real_executable,
        ack_path=str(ack_path), log_path=str(update_log_path()))
    script_path = _write_helper_script(f"helper-installed-{ctx.version_label}.ps1", script)
    log_update_event(f"launching installed-update helper for {ctx.version_label}")
    return launch_detached_helper(_powershell_argv(script_path), cwd=root)


def prepare_windows_portable(staged_exe, ctx: InstallContext) -> subprocess.Popen:
    """Готовит и запускает хелпер замены портативного exe через .bak."""
    root = staging_root()
    root.mkdir(parents=True, exist_ok=True)
    ack_path = root / f"ack-{ctx.version_label}.json"
    try:
        ack_path.unlink()
    except OSError:
        pass
    script = render_windows_portable_script(
        app_pid=ctx.app_pid, staged_exe=str(staged_exe),
        real_exe=ctx.real_executable, ack_path=str(ack_path),
        log_path=str(update_log_path()))
    script_path = _write_helper_script(f"helper-portable-{ctx.version_label}.ps1", script)
    log_update_event(f"launching portable-update helper for {ctx.version_label}")
    return launch_detached_helper(_powershell_argv(script_path), cwd=root)
