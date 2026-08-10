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


# --- Git-источники --------------------------------------------------------

@dataclass(frozen=True)
class GitUpdateResult:
    old_head: str
    new_head: str
    requirements_changed: bool
    recovery_hint: str


def install_git_update(root, run=subprocess.run, pip_argv=None) -> GitUpdateResult:
    """Fast-forward-only pull с autostash и проверяемыми зависимостями.

    Никогда не делает reset: расхождение с upstream — ошибка с инструкцией.
    Каждая ошибка содержит старый HEAD и путь восстановления.
    """
    def git(args, timeout=60):
        return run(["git", *args], cwd=str(root), capture_output=True, text=True,
                   timeout=timeout, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})

    def fail(message, res=None):
        detail = ""
        if res is not None:
            detail = f": {(res.stderr or res.stdout or '').strip()[:400]}"
        raise UpdateInstallError(f"{message}{detail}\n{hint}")

    head = git(["rev-parse", "HEAD"])
    if head.returncode != 0:
        raise UpdateInstallError(
            f"Не удалось определить текущий коммит: {(head.stderr or '').strip()[:400]}")
    old_head = head.stdout.strip()
    hint = (f"Прежнее состояние: git reset --hard {old_head[:12]} "
            "(см. также git reflog и git stash list).")

    upstream = git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])
    if upstream.returncode != 0:
        raise UpdateInstallError(
            "Для этой копии не настроена upstream-ветка — автообновление "
            "невозможно (git branch --set-upstream-to=origin/<ветка>).")

    fetch = git(["fetch"])
    if fetch.returncode != 0:
        fail("git fetch завершился с ошибкой", fetch)

    counts = git(["rev-list", "--left-right", "--count", "HEAD...@{u}"])
    if counts.returncode != 0:
        fail("Не удалось сравнить локальную ветку с upstream", counts)
    try:
        ahead, _behind = (int(x) for x in counts.stdout.split())
    except ValueError:
        fail("Не удалось сравнить локальную ветку с upstream", counts)
    if ahead > 0:
        raise UpdateInstallError(
            f"Локальная ветка разошлась с upstream ({ahead} локальных коммитов). "
            f"Автообновление не выполняет reset — слейте изменения вручную.\n{hint}")

    pull = git(["pull", "--ff-only", "--autostash", "--no-edit"], timeout=300)
    if pull.returncode != 0:
        fail("git pull завершился с ошибкой", pull)

    new_head_res = git(["rev-parse", "HEAD"])
    new_head = new_head_res.stdout.strip() if new_head_res.returncode == 0 else ""

    req_diff = git(["diff", "--name-only", f"{old_head}..HEAD", "--", "requirements.txt"])
    requirements_changed = bool(req_diff.stdout.strip())
    if requirements_changed:
        argv = pip_argv or [sys.executable, "-m", "pip", "install", "-r",
                            str(Path(root) / "requirements.txt")]
        pip = run(argv, capture_output=True, text=True, timeout=600)
        if pip.returncode != 0:
            fail("Код обновлён, но установка зависимостей не удалась", pip)
    log_update_event(f"git update {old_head[:12]} -> {new_head[:12]}")
    return GitUpdateResult(old_head, new_head, requirements_changed, hint)


# --- Source-архив: python-хелпер с журналом -------------------------------

_ARCHIVE_HELPER_TEMPLATE = r'''# -*- coding: utf-8 -*-
"""Отсоединённый хелпер обновления source-архива (сгенерирован апдейтером)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

PID = @@PID@@
ZIP_PATH = @@ZIP@@
ROOT = @@ROOT@@
JOURNAL = @@JOURNAL@@
ACK = @@ACK@@
LOG = @@LOG@@
COMMIT = @@COMMIT@@
PYTHON_ARGV = @@PYTHON_ARGV@@
PIP_ARGV = @@PIP_ARGV@@
IDENTITY_NAME = ".translator-update.json"
WAIT_ITER = @@WAIT_ITER@@
HEALTH_ITER = @@HEALTH_ITER@@


def log(message):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write("[%s] [UPD] %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), message))
    except OSError:
        pass


def pid_alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def clean_env():
    env = {k: v for k, v in os.environ.items() if not k.startswith("_PYI_")}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def main():
    for _ in range(WAIT_ITER):
        if not pid_alive(PID):
            break
        time.sleep(0.5)
    if pid_alive(PID):
        log("app still running; aborting untouched")
        return 1

    identity_path = os.path.join(ROOT, IDENTITY_NAME)
    old_identity = None
    try:
        with open(identity_path, "r", encoding="utf-8") as f:
            old_identity = json.load(f)
    except (OSError, ValueError):
        old_identity = None
    old_files = set((old_identity or {}).get("files") or [])

    # Разбор архива: срез верхнего каталога, отказ от абсолютных путей и ..
    entries = []
    new_files = []
    with zipfile.ZipFile(ZIP_PATH) as z:
        names = z.namelist()
        tops = set(n.split("/", 1)[0] for n in names if n.strip("/"))
        prefix = (tops.pop() + "/") if len(tops) == 1 else ""
        for member in names:
            rel = member[len(prefix):] if member.startswith(prefix) else member
            if not rel or rel.endswith("/"):
                continue
            norm = os.path.normpath(rel)
            if os.path.isabs(norm) or norm.startswith("..") or norm.startswith(os.sep):
                log("refusing unsafe archive member: %r" % member)
                return 1
            entries.append((norm, member))
            new_files.append(norm.replace(os.sep, "/"))

    to_remove = sorted(old_files - set(new_files))
    files_dir = os.path.join(JOURNAL, "files")
    os.makedirs(files_dir, exist_ok=True)
    added = []

    def backup(rel):
        src = os.path.join(ROOT, rel)
        if os.path.isfile(src):
            dst = os.path.join(files_dir, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)

    def restore():
        for rel in added:
            try:
                os.remove(os.path.join(ROOT, rel))
            except OSError:
                pass
        for base, _dirs, files in os.walk(files_dir):
            for name in files:
                src = os.path.join(base, name)
                rel = os.path.relpath(src, files_dir)
                dst = os.path.join(ROOT, rel)
                try:
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(src, dst)
                except OSError:
                    log("restore failed for %s" % rel)
        old_id = os.path.join(JOURNAL, "identity.json")
        try:
            if os.path.isfile(old_id):
                shutil.copy2(old_id, identity_path)
            elif os.path.isfile(identity_path):
                os.remove(identity_path)
        except OSError:
            pass

    try:
        for rel, _member in entries:
            backup(rel)
        for rel in to_remove:
            backup(rel)
        if os.path.isfile(identity_path):
            shutil.copy2(identity_path, os.path.join(JOURNAL, "identity.json"))

        with zipfile.ZipFile(ZIP_PATH) as z:
            for rel, member in entries:
                target = os.path.join(ROOT, rel)
                os.makedirs(os.path.dirname(target) or ROOT, exist_ok=True)
                existed = os.path.exists(target)
                with z.open(member) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                if not existed:
                    added.append(rel)
        for rel in to_remove:
            try:
                os.remove(os.path.join(ROOT, rel))
            except OSError:
                pass
        with open(identity_path, "w", encoding="utf-8") as f:
            json.dump({"schema": 1, "commit": COMMIT, "files": sorted(new_files)}, f,
                      ensure_ascii=False, indent=2)
        if os.path.isfile(os.path.join(ROOT, "requirements.txt")):
            pip = subprocess.run(PIP_ARGV, cwd=ROOT)
            if pip.returncode != 0:
                raise RuntimeError("pip install failed with code %s" % pip.returncode)
    except Exception as e:  # noqa: BLE001
        log("apply failed: %s; restoring journal" % e)
        restore()
        return 1

    try:
        os.remove(ACK)
    except OSError:
        pass
    env = clean_env()
    env["GT_UPDATE_ACK_FILE"] = ACK
    proc = subprocess.Popen(PYTHON_ARGV, cwd=ROOT, env=env)
    for _ in range(HEALTH_ITER):
        if os.path.isfile(ACK):
            log("health ack received; cleaning up")
            shutil.rmtree(JOURNAL, ignore_errors=True)
            try:
                os.remove(ZIP_PATH)
            except OSError:
                pass
            return 0
        if proc.poll() is not None:
            log("new process exited without ack; restoring journal")
            restore()
            subprocess.Popen(PYTHON_ARGV, cwd=ROOT, env=clean_env())
            return 1
        time.sleep(0.5)
    log("HEALTH-TIMEOUT: process alive without ack; keeping journal")
    return 2


if __name__ == "__main__":
    sys.exit(main())
'''


def render_archive_helper(*, app_pid, zip_path, root, journal_dir, ack_path,
                          log_path, commit_sha, python_argv, pip_argv) -> str:
    return _render(_ARCHIVE_HELPER_TEMPLATE, {
        "PID": int(app_pid),
        "WAIT_ITER": APP_EXIT_WAIT_S * 2,
        "HEALTH_ITER": HEALTH_WINDOW_S * 2,
        "ZIP": repr(str(zip_path)),
        "ROOT": repr(str(root)),
        "JOURNAL": repr(str(journal_dir)),
        "ACK": repr(str(ack_path)),
        "LOG": repr(str(log_path)),
        "COMMIT": repr(str(commit_sha)),
        "PYTHON_ARGV": repr([str(a) for a in python_argv]),
        "PIP_ARGV": repr([str(a) for a in pip_argv]),
    })


def prepare_source_archive(staged_zip, root, ctx: InstallContext,
                           commit_sha: str) -> subprocess.Popen:
    """Готовит и запускает python-хелпер замены source-архива."""
    staging = staging_root()
    staging.mkdir(parents=True, exist_ok=True)
    label = ctx.version_label
    ack_path = staging / f"ack-{label}.json"
    try:
        ack_path.unlink()
    except OSError:
        pass
    script = render_archive_helper(
        app_pid=ctx.app_pid, zip_path=str(staged_zip), root=str(root),
        journal_dir=str(staging / f"journal-{label}"), ack_path=str(ack_path),
        log_path=str(update_log_path()), commit_sha=commit_sha,
        python_argv=[sys.executable, str(Path(root) / "main.py")],
        pip_argv=[sys.executable, "-m", "pip", "install", "-r",
                  str(Path(root) / "requirements.txt")])
    script_path = staging / f"helper-archive-{label}.py"
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(script)
    log_update_event(f"launching source-archive helper for {label}")
    return launch_detached_helper([sys.executable, str(script_path)], cwd=staging)


# --- macOS ----------------------------------------------------------------

_MACOS_TEMPLATE = r"""#!/bin/bash
exec >> @@LOG@@ 2>&1
log() { echo "[$(date -u +%FT%TZ)] [UPD] $1"; }
LIVE=@@BUNDLE@@
STAGED=@@STAGED@@
ACK=@@ACK@@
BIN=@@BIN@@
log "waiting for app pid @@PID@@"
for i in $(seq 1 @@WAIT_ITER@@); do kill -0 @@PID@@ 2>/dev/null || break; sleep 0.5; done
if kill -0 @@PID@@ 2>/dev/null; then log "app still running; aborting untouched"; exit 1; fi
MNT=""
EXTRACT=""
cleanup_source() {
  [ -n "$MNT" ] && hdiutil detach "$MNT" -force >/dev/null 2>&1
  [ -n "$EXTRACT" ] && rm -rf "$EXTRACT"
}
if @@IS_DMG@@; then
  MNT=$(hdiutil attach -nobrowse -readonly "$STAGED" | awk -F$'\t' '/\/Volumes\//{print $NF}' | tail -1)
  [ -d "$MNT" ] || { log "mount failed"; exit 1; }
  NEW_APP=$(find "$MNT" -maxdepth 1 -name '*.app' | head -1)
else
  EXTRACT=$(mktemp -d)
  unzip -q "$STAGED" -d "$EXTRACT" || { log "unzip failed"; cleanup_source; exit 1; }
  NEW_APP=$(find "$EXTRACT" -maxdepth 2 -name '*.app' | head -1)
fi
[ -d "$NEW_APP/Contents/MacOS" ] || { log "no valid .app in staged update"; cleanup_source; exit 1; }
codesign --verify --deep --strict "$NEW_APP" || { log "codesign verify failed"; cleanup_source; exit 1; }
rm -rf "$LIVE.new" "$LIVE.old"
ditto "$NEW_APP" "$LIVE.new" || { log "ditto failed"; cleanup_source; exit 1; }
cleanup_source
mv "$LIVE" "$LIVE.old" || { log "swap-out failed"; exit 1; }
if ! mv "$LIVE.new" "$LIVE"; then
  log "swap-in failed"
  mv "$LIVE.old" "$LIVE"
  exit 1
fi
rollback() {
  log "rolling back to previous bundle"
  rm -rf "$LIVE.rejected"
  mv "$LIVE" "$LIVE.rejected"
  mv "$LIVE.old" "$LIVE"
}
if ! xattr -cr "$LIVE"; then
  rollback
  "$LIVE/Contents/MacOS/$BIN" >/dev/null 2>&1 &
  exit 1
fi
rm -f "$ACK"
GT_UPDATE_ACK_FILE="$ACK" "$LIVE/Contents/MacOS/$BIN" >/dev/null 2>&1 &
NEWPID=$!
for i in $(seq 1 @@HEALTH_ITER@@); do
  if [ -f "$ACK" ]; then
    log "health ack received; cleaning up"
    rm -rf "$LIVE.old"
    rm -f "$STAGED"
    exit 0
  fi
  if ! kill -0 "$NEWPID" 2>/dev/null; then
    log "new process exited without ack; rolling back"
    rollback
    "$LIVE/Contents/MacOS/$BIN" >/dev/null 2>&1 &
    exit 1
  fi
  sleep 0.5
done
log "HEALTH-TIMEOUT: process alive without ack; keeping $LIVE.old and staged file"
exit 2"""


def render_macos_script(*, app_pid, staged, bundle, binary_name, ack, log, is_dmg) -> str:
    return _render(_MACOS_TEMPLATE, {
        "PID": int(app_pid),
        "WAIT_ITER": APP_EXIT_WAIT_S * 2,      # шаг 0.5 с
        "HEALTH_ITER": HEALTH_WINDOW_S * 2,
        "LOG": sh_quote(log),
        "BUNDLE": sh_quote(bundle),
        "STAGED": sh_quote(staged),
        "ACK": sh_quote(ack),
        "BIN": sh_quote(binary_name),
        "IS_DMG": "true" if is_dmg else "false",
    })


def find_app_bundle(executable: str):
    """Путь к .app-бандлу, содержащему executable, либо None."""
    path = os.path.abspath(executable)
    while path not in ("/", ""):
        if path.endswith(".app"):
            return path
        path = os.path.dirname(path)
    return None


def prepare_macos(staged_path, ctx: InstallContext) -> subprocess.Popen:
    """Готовит и запускает bash-хелпер замены .app c проверкой подписи."""
    bundle = find_app_bundle(ctx.real_executable)
    if bundle is None:
        raise UpdateInstallError(
            "Не удалось определить .app-бандл текущего приложения — "
            "обновление возможно только вручную")
    root = staging_root()
    root.mkdir(parents=True, exist_ok=True)
    ack_path = root / f"ack-{ctx.version_label}.json"
    try:
        ack_path.unlink()
    except OSError:
        pass
    script = render_macos_script(
        app_pid=ctx.app_pid, staged=str(staged_path), bundle=bundle,
        binary_name=os.path.basename(ctx.real_executable),
        ack=str(ack_path), log=str(update_log_path()),
        is_dmg=str(staged_path).lower().endswith(".dmg"))
    script_path = root / f"helper-macos-{ctx.version_label}.sh"
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(script)
    os.chmod(script_path, 0o700)
    log_update_event(f"launching macOS-update helper for {ctx.version_label}")
    return launch_detached_helper(["/bin/bash", str(script_path)], cwd=root)


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
