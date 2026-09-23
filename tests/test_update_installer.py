# -*- coding: utf-8 -*-
"""Тесты установочного слоя апдейтера (update_installer.py).

Все операции замены выполняются только во временных каталогах — реальные
исполняемые файлы и бандлы разработчика не трогаются никогда.
"""
import json
import os
import shutil
import sys
import time
import subprocess
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from gemini_translator.utils import update_installer as inst
from gemini_translator.version import __version__


# --- staging_root ---------------------------------------------------------

def test_staging_root_windows():
    env = {"LOCALAPPDATA": r"C:\Users\u\AppData\Local"}
    p = inst.staging_root(platform="win32", env=env)
    assert str(p).replace("\\", "/").endswith("AppData/Local/GeminiTranslator/updater")


def test_staging_root_darwin():
    env = {"HOME": "/Users/u"}
    p = inst.staging_root(platform="darwin", env=env)
    assert str(p).replace("\\", "/") == "/Users/u/Library/Application Support/GeminiTranslator/updater"


def test_staging_root_linux_xdg_and_fallback():
    p = inst.staging_root(platform="linux", env={"XDG_DATA_HOME": "/xdg", "HOME": "/home/u"})
    assert str(p).replace("\\", "/") == "/xdg/GeminiTranslator/updater"
    p2 = inst.staging_root(platform="linux", env={"HOME": "/home/u"})
    assert str(p2).replace("\\", "/") == "/home/u/.local/share/GeminiTranslator/updater"


# --- ack-протокол ---------------------------------------------------------

def test_write_startup_acknowledgement(tmp_path):
    ack = tmp_path / "sub" / "ack.json"
    env = {inst.ACK_ENV: str(ack)}
    inst.write_startup_acknowledgement(env=env)
    data = json.loads(ack.read_text())
    assert data["pid"] == os.getpid()
    assert data["version"] == __version__
    assert inst.ACK_ENV not in env


def test_write_startup_acknowledgement_noop_and_never_raises(tmp_path):
    inst.write_startup_acknowledgement(env={})  # нет переменной — тихий no-op
    blocker = tmp_path / "file"
    blocker.write_text("x")
    # путь «внутри файла» — запись невозможна, но исключения быть не должно
    inst.write_startup_acknowledgement(env={inst.ACK_ENV: str(blocker / "ack.json")})


def test_write_startup_acknowledgement_never_exposes_partial_file(tmp_path, monkeypatch):
    # Хелпер читает версию из ack, как только увидит файл: сбой посреди
    # записи не должен оставить недописанный JSON под итоговым именем.
    monkeypatch.setattr(inst, "__version__", object())  # JSON падает на середине
    ack = tmp_path / "ack.json"
    inst.write_startup_acknowledgement(env={inst.ACK_ENV: str(ack)})
    assert list(tmp_path.iterdir()) == []  # ни ack, ни временного файла


# --- чистка staging -------------------------------------------------------

def test_cleanup_stale_staging(tmp_path):
    old_file = tmp_path / "old.part"
    old_file.write_text("x")
    old_dir = tmp_path / "backup-v1"
    old_dir.mkdir()
    (old_dir / "f").write_text("x")
    fresh = tmp_path / "fresh.exe"
    fresh.write_text("x")
    log = tmp_path / "updater.log"
    log.write_text("log")
    stale_time = time.time() - 9 * 86400
    for p in (old_file, old_dir, log):
        os.utime(p, (stale_time, stale_time))
    inst.cleanup_stale_staging(max_age_days=7, root=tmp_path)
    assert not old_file.exists() and not old_dir.exists()
    assert fresh.exists() and log.exists()


def test_cleanup_stale_staging_missing_root_is_noop(tmp_path):
    inst.cleanup_stale_staging(root=tmp_path / "nope")


# --- get_real_executable (перенос из HomePage) ----------------------------

def test_get_real_executable_not_frozen(monkeypatch):
    monkeypatch.setattr(sys, "executable", "/usr/bin/python3")
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert inst.get_real_executable() == os.path.abspath("/usr/bin/python3")


def test_get_real_executable_frozen_normal(monkeypatch):
    monkeypatch.setattr(sys, "executable", r"C:\Program Files\App\myapp.exe")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    result = inst.get_real_executable()
    assert "_MEI" not in result and "myapp.exe" in result


def test_get_real_executable_frozen_mei_fallback(monkeypatch):
    mei = r"C:\Users\Admin\AppData\Local\Temp\_MEI140642\translatorFork_MOD.exe"
    real = r"C:\Users\Admin\Desktop\translatorFork_MOD.exe"
    monkeypatch.setattr(sys, "executable", mei)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "argv", [real])
    assert inst.get_real_executable() == os.path.abspath(real)


def test_get_real_executable_frozen_darwin(monkeypatch):
    mac_exe = "/Applications/GeminiTranslator.app/Contents/MacOS/GeminiTranslator"
    monkeypatch.setattr(sys, "executable", mac_exe)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "darwin")
    assert inst.get_real_executable() == os.path.abspath(mac_exe)


# --- окружение и запуск хелперов ------------------------------------------

def test_clean_helper_env(monkeypatch):
    monkeypatch.setenv("_PYI_SPLASH_IPC", "1")
    monkeypatch.setenv("KEEP_ME", "yes")
    env = inst.clean_helper_env()
    assert "_PYI_SPLASH_IPC" not in env
    assert env["KEEP_ME"] == "yes"
    assert env["PYINSTALLER_RESET_ENVIRONMENT"] == "1"


def test_launch_detached_helper_starts_process(tmp_path):
    popen = inst.launch_detached_helper(
        [sys.executable, "-c", "import time; time.sleep(2)"], cwd=tmp_path)
    try:
        assert popen.poll() is None
    finally:
        popen.kill()
        popen.wait()


def test_launch_detached_helper_failure_raises(tmp_path):
    with pytest.raises(inst.UpdateInstallError):
        inst.launch_detached_helper([str(tmp_path / "no-such-binary")], cwd=tmp_path)


# --- квотирование ---------------------------------------------------------

def test_ps_quote():
    assert inst.ps_quote("it's") == "'it''s'"
    assert inst.ps_quote(r"C:\a b\c.exe") == r"'C:\a b\c.exe'"


def test_sh_quote():
    assert inst.sh_quote("/a b/c's") == "'/a b/c'\"'\"'s'"


# --- Windows-хелперы (Task 7) ---------------------------------------------

_TRICKY_EXE = r"C:\Program Files\it's\translatorFork_MOD.exe"


def _installed_script(setup_log_path=r"C:\stage\setup-v10.5.22.log"):
    return inst.render_windows_installed_script(
        app_pid=4242,
        setup_path=r"C:\stage\GeminiTranslator-Setup.exe",
        app_dir=r"C:\Program Files\it's",
        backup_dir=r"C:\stage\backup-v10.5.22",
        real_exe=_TRICKY_EXE,
        ack_path=r"C:\stage\ack-v10.5.22.json",
        log_path=r"C:\stage\updater.log",
        attempt_log_path=r"C:\stage\attempt-v10.5.22.log",
        setup_log_path=setup_log_path,
        expected_version="10.5.22",
    )


def _portable_script():
    return inst.render_windows_portable_script(
        app_pid=4242,
        staged_exe=r"C:\stage\GeminiTranslator-Portable.exe",
        real_exe=_TRICKY_EXE,
        ack_path=r"C:\stage\ack-v10.5.22.json",
        log_path=r"C:\stage\updater.log",
        attempt_log_path=r"C:\stage\attempt-v10.5.22.log",
    )


@pytest.mark.parametrize("render", [_installed_script, _portable_script])
def test_windows_scripts_wait_on_pid_and_health(render):
    content = render()
    assert f"Wait-Process -Id 4242 -Timeout {inst.APP_EXIT_WAIT_S}" in content
    assert "aborting untouched" in content
    assert "HEALTH-TIMEOUT" in content
    assert content.rstrip().endswith("exit 2")  # живой процесс — оставить всё как есть
    assert "FORCECLOSEAPPLICATIONS" not in content
    # путь с апострофом и пробелом обязан пройти через ps_quote
    assert "'C:\\Program Files\\it''s\\translatorFork_MOD.exe'" in content
    # ack удаляется до установки переменной окружения
    assert content.index("Remove-Item -LiteralPath 'C:\\stage\\ack-v10.5.22.json'") \
        < content.index("$env:GT_UPDATE_ACK_FILE")
    assert "exited without ack" in content


def test_windows_installed_script_specifics():
    content = _installed_script()
    assert "'/NORESTART'" in content and "'/VERYSILENT'" in content
    assert content.count("robocopy") >= 2  # snapshot + restore
    assert "snapshot failed; aborting untouched" in content
    assert "setup failed" in content


@pytest.mark.skipif(shutil.which("powershell") is None,
                    reason="парсер есть только в Windows PowerShell")
@pytest.mark.parametrize("render", [_installed_script, _portable_script])
def test_windows_scripts_parse_in_powershell(render, tmp_path):
    # Остальные тесты хелперов сверяют текст; синтаксис PowerShell проверяет
    # только его собственный парсер (в CI на windows-latest).
    script = tmp_path / "helper.ps1"
    script.write_text(render(), encoding="utf-8-sig")
    check = (
        "$errors = $null; "
        "[System.Management.Automation.Language.Parser]::ParseFile("
        f"{inst.ps_quote(script)}, [ref]$null, [ref]$errors) | Out-Null; "
        "if ($errors) { $errors | ForEach-Object { $_.Message }; exit 1 }"
    )
    proc = subprocess.run(["powershell", "-NoProfile", "-Command", check],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.parametrize("render", [_installed_script, _portable_script])
def test_windows_scripts_copy_lines_to_attempt_log(render):
    # Следующий запуск программы объясняет итог по строкам этой попытки.
    log_fn = next(line for line in render().splitlines() if line.startswith("function Log"))
    assert "'C:\\stage\\updater.log'" in log_fn
    assert "'C:\\stage\\attempt-v10.5.22.log'" in log_fn


def test_windows_installed_script_keeps_setup_log():
    content = _installed_script(setup_log_path=r"C:\Users\O'Neil Smith\stage\setup-v10.5.22.log")
    setup_call = next(line for line in content.splitlines()
                      if line.startswith("$setup = Start-Process"))
    # Start-Process склеивает аргументы через пробел: кавычки внутри элемента
    # держат путь с пробелом целым, апостроф удваивает ps_quote.
    assert "'/LOG=\"C:\\Users\\O''Neil Smith\\stage\\setup-v10.5.22.log\"'" in setup_call
    failure = next(line for line in content.splitlines() if "setup failed with exit code" in line)
    assert "setup-v10.5.22.log" in failure  # из журнала видно, где искать подробности


def test_windows_installed_script_rejects_ack_from_other_version():
    content = _installed_script()
    health = content[content.index("while ((Get-Date) -lt $deadline)"):]
    # версия из ack сверяется до того, как помощник объявит успех
    assert health.index("ConvertFrom-Json") < health.index("health ack received")
    assert "-ne '10.5.22'" in health
    mismatch = health[health.index("VERSION-MISMATCH"):]
    mismatch = mismatch[:mismatch.index("exit")]
    # запущена чужая копия: бэкап и установщик нужны для разбора и ручной установки
    assert "Remove-Item" not in mismatch


def test_windows_portable_script_specifics():
    content = _portable_script()
    assert "'.bak'" in content
    assert "/VERYSILENT" not in content
    assert ".rejected" in content


def _ctx(tmp_path, exe_name="translatorFork_MOD.exe", expected_version="10.5.22"):
    exe = tmp_path / "app" / exe_name
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_bytes(b"MZ")
    return inst.InstallContext(app_pid=os.getpid(), real_executable=str(exe),
                               version_label="v10.5.22",
                               expected_version=expected_version)


def test_prepare_windows_installed_free_space_guard(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    staged = tmp_path / "GeminiTranslator-Setup.exe"
    staged.write_bytes(b"MZ")
    monkeypatch.setattr(inst, "staging_root", lambda **kw: tmp_path / "staging")
    monkeypatch.setattr(inst, "directory_size", lambda p: 10**18)
    with pytest.raises(inst.UpdateInstallError):
        inst.prepare_windows_installed(staged, ctx)


def test_prepare_windows_installed_launches_powershell(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    staged = tmp_path / "GeminiTranslator-Setup.exe"
    staged.write_bytes(b"MZ")
    monkeypatch.setattr(inst, "staging_root", lambda **kw: tmp_path / "staging")
    monkeypatch.setattr(inst, "directory_size", lambda p: 1)
    captured = {}

    def fake_launch(argv, *, cwd):
        captured["argv"] = argv
        captured["cwd"] = cwd
        return object()

    monkeypatch.setattr(inst, "launch_detached_helper", fake_launch)
    inst.prepare_windows_installed(staged, ctx)
    argv = captured["argv"]
    assert argv[:4] == ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass"]
    assert argv[4] == "-File"
    script = Path(argv[5])
    content = script.read_text(encoding="utf-8")
    assert script.exists() and "Wait-Process" in content
    assert str(tmp_path / "staging" / "setup-v10.5.22.log") in content
    assert "-ne '10.5.22'" in content


def test_prepare_windows_installed_requires_expected_version(tmp_path, monkeypatch):
    # Без ожидаемой версии хелпер снова принял бы любое подтверждение.
    ctx = _ctx(tmp_path, expected_version="")
    staged = tmp_path / "GeminiTranslator-Setup.exe"
    staged.write_bytes(b"MZ")
    monkeypatch.setattr(inst, "staging_root", lambda **kw: tmp_path / "staging")
    monkeypatch.setattr(inst, "directory_size", lambda p: 1)
    launched = []
    monkeypatch.setattr(inst, "launch_detached_helper",
                        lambda argv, *, cwd: launched.append(argv))
    with pytest.raises(inst.UpdateInstallError):
        inst.prepare_windows_installed(staged, ctx)
    assert launched == []


def test_prepare_windows_portable_launches_powershell(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path, exe_name="GeminiTranslator-Portable.exe")
    staged = tmp_path / "staged.exe"
    staged.write_bytes(b"MZ")
    monkeypatch.setattr(inst, "staging_root", lambda **kw: tmp_path / "staging")
    captured = {}
    monkeypatch.setattr(inst, "launch_detached_helper",
                        lambda argv, *, cwd: captured.update(argv=argv) or object())
    inst.prepare_windows_portable(staged, ctx)
    script = Path(captured["argv"][5])
    assert ".bak" in script.read_text(encoding="utf-8")


# --- macOS-хелпер (Task 8) ------------------------------------------------

def _macos_script(bundle="/Apps/x y/GeminiTranslator.app", is_dmg=False):
    return inst.render_macos_script(
        app_pid=4242,
        staged="/stage/GeminiTranslator-macOS.zip",
        bundle=bundle,
        binary_name="GeminiTranslator",
        ack="/stage/ack.json",
        log="/stage/updater.log",
        attempt="/stage/attempt-v10.5.22.log",
        is_dmg=is_dmg,
    )


def test_macos_script_verifies_before_swap():
    content = _macos_script()
    # codesign проверяет staged-копию строго до первого mv живого бандла
    assert content.index("codesign --verify --deep --strict") < content.index('mv "$LIVE"')
    assert "Contents/MacOS" in content  # проверка структуры бандла
    assert "'/Apps/x y/GeminiTranslator.app'" in content  # sh_quote
    assert "HEALTH-TIMEOUT" in content
    assert content.rstrip().endswith("exit 2")
    # откат при сбое снятия карантина: непроверенный запуск запрещён
    xattr_branch = content[content.index("xattr -cr"):]
    assert "rollback" in xattr_branch[:200]


def test_macos_script_dmg_and_zip_variants():
    dmg = _macos_script(is_dmg=True)
    assert "hdiutil attach -nobrowse -readonly" in dmg
    assert "unzip" in _macos_script(is_dmg=False)


@pytest.fixture
def macos_fixture(tmp_path):
    """Живой Old.app + staged-zip с New.app + шимы codesign/xattr/hdiutil."""
    if sys.platform != "darwin":
        pytest.skip("исполняемый тест только на macOS")
    result = tmp_path / "result.txt"
    live = tmp_path / "GeminiTranslator.app"
    macos_dir = live / "Contents" / "MacOS"
    macos_dir.mkdir(parents=True)
    old_bin = macos_dir / "GeminiTranslator"
    old_bin.write_text(f'#!/bin/bash\necho old >> "{result}"\n')
    old_bin.chmod(0o755)

    build = tmp_path / "build"
    new_macos = build / "New.app" / "Contents" / "MacOS"
    new_macos.mkdir(parents=True)
    shim_log = tmp_path / "shims.log"

    def make_new_binary(script_body):
        new_bin = new_macos / "GeminiTranslator"
        new_bin.write_text(script_body)
        new_bin.chmod(0o755)
        staged = tmp_path / "staged.zip"
        if staged.exists():
            staged.unlink()
        subprocess.run(["zip", "-qr", str(staged), "New.app"], cwd=build, check=True)
        return staged

    shims = tmp_path / "shims"
    shims.mkdir()
    for tool in ("codesign", "xattr", "hdiutil"):
        shim = shims / tool
        shim.write_text(f'#!/bin/bash\necho "{tool} $@" >> "{shim_log}"\nexit 0\n')
        shim.chmod(0o755)

    attempt = tmp_path / "attempt.log"

    def run_helper(staged):
        ack = tmp_path / "ack.json"
        log = tmp_path / "updater.log"
        # «старое приложение» — реальный процесс; дожидаемся его выхода до
        # запуска хелпера: незажатый (reaped) PID сразу отпускает kill -0,
        # а зомби держал бы цикл ожидания все 120 секунд.
        app_proc = subprocess.Popen(["sleep", "0.2"])
        pid = app_proc.pid
        app_proc.wait()
        script = inst.render_macos_script(
            app_pid=pid, staged=str(staged), bundle=str(live),
            binary_name="GeminiTranslator", ack=str(ack), log=str(log),
            attempt=str(attempt), is_dmg=False)
        script_path = tmp_path / "helper.sh"
        script_path.write_text(script)
        env = dict(os.environ)
        env["PATH"] = f"{shims}:{env['PATH']}"
        proc = subprocess.run(["bash", str(script_path)], env=env, timeout=60)
        return proc, ack, log

    return {
        "live": live, "result": result, "shim_log": shim_log,
        "make_new_binary": make_new_binary, "run_helper": run_helper,
        "tmp": tmp_path, "attempt": attempt,
    }


def test_macos_helper_happy_path_swaps_and_cleans(macos_fixture):
    f = macos_fixture
    staged = f["make_new_binary"](
        '#!/bin/bash\necho new >> "%s"\necho ok > "$GT_UPDATE_ACK_FILE"\nsleep 1\n'
        % f["result"])
    proc, ack, log = f["run_helper"](staged)
    assert proc.returncode == 0, log.read_text() if log.exists() else "no log"
    live_bin = f["live"] / "Contents" / "MacOS" / "GeminiTranslator"
    assert "new" in live_bin.read_text()  # живой бандл заменён
    assert not (f["tmp"] / "GeminiTranslator.app.old").exists()  # бэкап удалён
    assert not staged.exists()  # staged-архив удалён после подтверждения
    assert ack.exists()
    shim_lines = f["shim_log"].read_text().splitlines()
    codesign_lines = [l for l in shim_lines if l.startswith("codesign")]
    xattr_lines = [l for l in shim_lines if l.startswith("xattr")]
    # верификация шла по staged-копии, карантин снимался с живого бандла
    assert codesign_lines and str(f["live"]) not in codesign_lines[0]
    assert xattr_lines and str(f["live"]) in xattr_lines[0]
    # строки попытки дублируются в отдельный журнал для следующего запуска
    assert "health ack received" in f["attempt"].read_text()
    assert "health ack received" in log.read_text()


def test_macos_helper_rolls_back_without_ack(macos_fixture):
    f = macos_fixture
    staged = f["make_new_binary"]('#!/bin/bash\nexit 1\n')
    proc, ack, log = f["run_helper"](staged)
    assert proc.returncode == 1
    live_bin = f["live"] / "Contents" / "MacOS" / "GeminiTranslator"
    assert "old" in live_bin.read_text()  # живой бандл вернулся к старому
    assert (f["tmp"] / "GeminiTranslator.app.rejected").exists()
    assert staged.exists()  # staged сохранён для ручного разбора
    assert not ack.exists()
    assert "exited without ack" in f["attempt"].read_text()


def test_prepare_macos_requires_bundle(tmp_path, monkeypatch):
    ctx = inst.InstallContext(app_pid=1, real_executable="/usr/local/bin/tool",
                              version_label="v10.5.22")
    with pytest.raises(inst.UpdateInstallError):
        inst.prepare_macos(tmp_path / "x.dmg", ctx)


def test_prepare_macos_launches_bash(tmp_path, monkeypatch):
    exe = tmp_path / "GeminiTranslator.app" / "Contents" / "MacOS" / "GeminiTranslator"
    exe.parent.mkdir(parents=True)
    exe.write_text("bin")
    ctx = inst.InstallContext(app_pid=1, real_executable=str(exe),
                              version_label="v10.5.22")
    monkeypatch.setattr(inst, "staging_root", lambda **kw: tmp_path / "staging")
    captured = {}
    monkeypatch.setattr(inst, "launch_detached_helper",
                        lambda argv, *, cwd: captured.update(argv=argv) or object())
    inst.prepare_macos(tmp_path / "u.dmg", ctx)
    assert captured["argv"][0] == "/bin/bash"
    content = Path(captured["argv"][1]).read_text()
    assert "hdiutil attach" in content  # .dmg → dmg-ветка


# --- Git-установка (Task 9) -----------------------------------------------

def _git_run(responses):
    """Скриптованный subprocess.run: [(подстрока, (rc, out, err))], первый матч."""
    calls = []

    def fake_run(cmd, **kw):
        calls.append((list(cmd), kw))
        joined = " ".join(str(c) for c in cmd)
        for substr, (rc, out, err) in responses:
            if substr in joined:
                return subprocess.CompletedProcess(cmd, rc, out, err)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    return fake_run, calls


_OLD = "1" * 40
_NEW = "2" * 40


def _happy_git_responses(req_changed=False, pip_rc=0):
    first = {"used": False}

    def head_response(_first=first):
        # первый rev-parse HEAD — старый, после pull — новый
        if not _first["used"]:
            _first["used"] = True
            return (0, _OLD + "\n", "")
        return (0, _NEW + "\n", "")

    return [
        ("--abbrev-ref", (0, "origin/main\n", "")),
        ("--left-right", (0, "0\t5\n", "")),
        ("fetch", (0, "", "")),
        ("pull", (0, "", "")),
        ("diff", (0, "requirements.txt\n" if req_changed else "", "")),
        ("pip", (pip_rc, "", "pip boom" if pip_rc else "")),
        ("rev-parse", head_response),
    ]


def _run_with_dynamic(responses):
    calls = []

    def fake_run(cmd, **kw):
        calls.append((list(cmd), kw))
        joined = " ".join(str(c) for c in cmd)
        for substr, resp in responses:
            if substr in joined:
                rc, out, err = resp() if callable(resp) else resp
                return subprocess.CompletedProcess(cmd, rc, out, err)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    return fake_run, calls


def test_install_git_update_happy_path(tmp_path):
    run, calls = _run_with_dynamic(_happy_git_responses())
    result = inst.install_git_update(tmp_path, run=run)
    assert result.old_head == _OLD and result.new_head == _NEW
    assert result.requirements_changed is False
    assert _OLD[:12] in result.recovery_hint
    joined_all = [" ".join(c) for c, _ in calls]
    assert any("--ff-only" in j and "--autostash" in j for j in joined_all)
    for cmd, kw in calls:
        if cmd[0] == "git":
            assert kw.get("env", {}).get("GIT_TERMINAL_PROMPT") == "0"
            assert kw.get("timeout")


def test_install_git_update_requirements_change_runs_pip(tmp_path):
    run, calls = _run_with_dynamic(_happy_git_responses(req_changed=True))
    result = inst.install_git_update(tmp_path, run=run,
                                     pip_argv=["pip", "install", "-r", "requirements.txt"])
    assert result.requirements_changed is True
    assert any(c[0] == "pip" for c, _ in calls)


def test_install_git_update_pip_failure_keeps_recovery_info(tmp_path):
    run, _ = _run_with_dynamic(_happy_git_responses(req_changed=True, pip_rc=1))
    with pytest.raises(inst.UpdateInstallError) as e:
        inst.install_git_update(tmp_path, run=run,
                                pip_argv=["pip", "install", "-r", "requirements.txt"])
    assert _OLD[:12] in str(e.value)


def test_install_git_update_missing_upstream(tmp_path):
    run, _ = _git_run([("rev-parse HEAD", (0, _OLD + "\n", "")),
                       ("--abbrev-ref", (1, "", "no upstream"))])
    with pytest.raises(inst.UpdateInstallError) as e:
        inst.install_git_update(tmp_path, run=run)
    assert "upstream" in str(e.value)


def test_install_git_update_divergence_never_resets(tmp_path):
    run, calls = _git_run([
        ("--abbrev-ref", (0, "origin/main\n", "")),
        ("--left-right", (0, "2\t5\n", "")),
        ("rev-parse", (0, _OLD + "\n", "")),
    ])
    with pytest.raises(inst.UpdateInstallError) as e:
        inst.install_git_update(tmp_path, run=run)
    assert "разошлась" in str(e.value) and _OLD[:12] in str(e.value)
    assert not any("reset" in " ".join(c) for c, _ in calls)
    assert not any("pull" in " ".join(c) for c, _ in calls)


def test_install_git_update_pull_failure_has_old_head(tmp_path):
    run, _ = _run_with_dynamic([
        ("--abbrev-ref", (0, "origin/main\n", "")),
        ("--left-right", (0, "0\t5\n", "")),
        ("fetch", (0, "", "")),
        ("pull", (1, "", "error: autostash conflict")),
        ("rev-parse", (0, _OLD + "\n", "")),
    ])
    with pytest.raises(inst.UpdateInstallError) as e:
        inst.install_git_update(tmp_path, run=run)
    assert "autostash conflict" in str(e.value) and _OLD[:12] in str(e.value)


# --- Source-архив: хелпер с журналом (Task 9) -----------------------------

def _archive_env(tmp_path, app_script, zip_extra=None, old_identity=None):
    """Собирает фейковый source-корень + staged-zip + рендерит хелпер."""
    root = tmp_path / "root"
    root.mkdir()
    (root / "keep_me.txt").write_text("user file")
    (root / "obsolete.txt").write_text("old managed")
    (root / "changed.py").write_text("old code")
    identity = old_identity if old_identity is not None else {
        "schema": 2, "commit": _OLD,
        "repository": "example/project",
        "files": ["obsolete.txt", "changed.py", "fake_app.py"]}
    (root / ".translator-update.json").write_text(json.dumps(identity))
    (root / "fake_app.py").write_text("print('old app')")

    build = tmp_path / "zip-build" / f"repo-{_NEW[:7]}"
    build.mkdir(parents=True)
    (build / "changed.py").write_text("new code")
    (build / "added.py").write_text("brand new")
    (build / "fake_app.py").write_text(app_script)
    for rel, content in (zip_extra or {}).items():
        p = build / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    staged = tmp_path / "staged.zip"
    with zipfile.ZipFile(staged, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in build.rglob("*"):
            if path.is_file():
                archive.write(path, Path(build.name) / path.relative_to(build))

    journal = tmp_path / "journal"
    ack = tmp_path / "ack.json"
    log = tmp_path / "updater.log"
    script = inst.render_archive_helper(
        app_pid=99_999_999, zip_path=str(staged), root=str(root),
        journal_dir=str(journal), ack_path=str(ack), log_path=str(log),
        commit_sha=_NEW,
        repository="example/project",
        python_argv=[sys.executable, str(root / "fake_app.py")],
        pip_argv=[sys.executable, "-c", "import sys; sys.exit(0)"])
    helper = tmp_path / "helper.py"
    helper.write_text(script, encoding="utf-8")
    return root, staged, helper, ack, journal


def test_archive_helper_script_content():
    script = inst.render_archive_helper(
        app_pid=1, zip_path="/z.zip", root="/root", journal_dir="/j",
        ack_path="/ack", log_path="/log", commit_sha=_NEW,
        repository="example/project",
        python_argv=["python", "main.py"], pip_argv=["pip", "install"])
    assert "HEALTH-TIMEOUT" in script
    assert ".." in script and "restore" in script.lower()
    assert "GT_UPDATE_ACK_FILE" in script


def test_archive_helper_applies_and_writes_identity(tmp_path):
    ack_writer = ("import json, os\n"
                  "open(os.environ['GT_UPDATE_ACK_FILE'], 'w').write('{}')\n"
                  "print('new app')\n")
    root, staged, helper, ack, journal = _archive_env(tmp_path, ack_writer)
    proc = subprocess.run([sys.executable, str(helper)], timeout=60,
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert (root / "changed.py").read_text() == "new code"
    assert (root / "added.py").exists()
    assert not (root / "obsolete.txt").exists()      # управляемый файл ушёл
    assert (root / "keep_me.txt").exists()           # пользовательский файл не тронут
    identity = json.loads((root / ".translator-update.json").read_text())
    assert identity["commit"] == _NEW
    assert identity["repository"] == "example/project"
    assert "added.py" in identity["files"] and "obsolete.txt" not in identity["files"]
    assert not journal.exists()                      # журнал убран после подтверждения
    assert not staged.exists()


def test_archive_helper_rolls_back_without_ack(tmp_path):
    root, staged, helper, ack, journal = _archive_env(tmp_path, "raise SystemExit(1)\n")
    proc = subprocess.run([sys.executable, str(helper)], timeout=60,
                          capture_output=True, text=True)
    assert proc.returncode == 1
    assert (root / "changed.py").read_text() == "old code"   # восстановлено
    assert not (root / "added.py").exists()                  # новые файлы убраны
    assert (root / "obsolete.txt").read_text() == "old managed"
    identity = json.loads((root / ".translator-update.json").read_text())
    assert identity["commit"] == _OLD                        # прежняя идентичность
    assert staged.exists()


def test_archive_helper_refuses_traversal(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    staged = tmp_path / "evil.zip"
    with zipfile.ZipFile(staged, "w") as z:
        z.writestr("top/../../evil.txt", "boom")
    script = inst.render_archive_helper(
        app_pid=99_999_999, zip_path=str(staged), root=str(root),
        journal_dir=str(tmp_path / "j"), ack_path=str(tmp_path / "a"),
        log_path=str(tmp_path / "l"), commit_sha=_NEW,
        repository="example/project",
        python_argv=[sys.executable, "-c", "pass"],
        pip_argv=[sys.executable, "-c", "pass"])
    helper = tmp_path / "helper.py"
    helper.write_text(script, encoding="utf-8")
    proc = subprocess.run([sys.executable, str(helper)], timeout=60,
                          capture_output=True, text=True)
    assert proc.returncode == 1
    assert not (tmp_path / "evil.txt").exists()


def _archive_helper_namespace(tmp_path):
    """Функции сгенерированного хелпера без запуска main()."""
    script = inst.render_archive_helper(
        app_pid=4242, zip_path=tmp_path / "u.zip", root=tmp_path / "src",
        journal_dir=tmp_path / "journal", ack_path=tmp_path / "ack.json",
        log_path=tmp_path / "updater.log", commit_sha=_NEW,
        repository="example/project",
        python_argv=[sys.executable, "-c", "pass"],
        pip_argv=[sys.executable, "-c", "pass"])
    namespace = {"__name__": "archive_helper_under_test"}
    exec(compile(script, "helper-archive.py", "exec"), namespace)
    return namespace


def test_archive_helper_pid_alive_avoids_os_kill_on_windows(tmp_path, monkeypatch):
    # На Windows сигнал 0 — это CTRL_C_EVENT: у хелпера без консоли
    # GenerateConsoleCtrlEvent не срабатывает, и CPython проваливается
    # в TerminateProcess — закрывающаяся программа убита, хелпер падает
    # с SystemError.
    helper = _archive_helper_namespace(tmp_path)

    def forbidden_kill(pid, sig):
        raise AssertionError("os.kill на Windows завершает процесс")

    probed = []
    helper["windows_pid_alive"] = lambda pid: probed.append(pid) or True
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(os, "kill", forbidden_kill)
    assert helper["pid_alive"](4242) is True
    assert probed == [4242]


def test_archive_helper_windows_pid_alive_reads_exit_code(tmp_path):
    windows_pid_alive = _archive_helper_namespace(tmp_path)["windows_pid_alive"]
    running = _FakeKernel32(exit_code=259)  # STILL_ACTIVE
    assert windows_pid_alive(42, kernel32=running)
    assert running.closed == [1234]
    finished = _FakeKernel32(exit_code=0)
    assert not windows_pid_alive(42, kernel32=finished)
    assert finished.closed == [1234]
    assert not windows_pid_alive(42, kernel32=_FakeKernel32(259, handle=0))


def test_prepare_source_archive_launches_helper(tmp_path, monkeypatch):
    monkeypatch.setattr(inst, "staging_root", lambda **kw: tmp_path / "staging")
    captured = {}
    monkeypatch.setattr(inst, "launch_detached_helper",
                        lambda argv, *, cwd: captured.update(argv=argv) or object())
    root = tmp_path / "src"
    root.mkdir()
    ctx = inst.InstallContext(app_pid=1, real_executable=sys.executable,
                              version_label=_NEW[:12])
    inst.prepare_source_archive(
        tmp_path / "u.zip",
        root,
        ctx,
        commit_sha=_NEW,
        repository="example/project",
    )
    assert captured["argv"][0] == sys.executable
    content = Path(captured["argv"][1]).read_text(encoding="utf-8")
    assert "HEALTH-TIMEOUT" in content


# --- итог прошлой установки ------------------------------------------------

def test_prepare_windows_installed_records_pending_update(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    staged = tmp_path / "GeminiTranslator-Setup.exe"
    staged.write_bytes(b"MZ")
    staging = tmp_path / "staging"
    staging.mkdir()
    monkeypatch.setattr(inst, "staging_root", lambda **kw: staging)
    monkeypatch.setattr(inst, "directory_size", lambda p: 1)
    stale = staging / "attempt-v10.5.22.log"
    stale.write_text("строка прошлой попытки\n", encoding="utf-8")
    monkeypatch.setattr(inst, "launch_detached_helper",
                        lambda argv, *, cwd: SimpleNamespace(pid=4321))
    inst.prepare_windows_installed(staged, ctx)
    pending = inst.read_pending_update()
    assert pending["version"] == "10.5.22"
    assert pending["label"] == "v10.5.22"
    assert pending["helper_pid"] == 4321
    assert pending["executable"] == ctx.real_executable
    assert not stale.exists()  # строки прошлой попытки не смешиваются с новыми


@pytest.mark.parametrize("channel", ["portable", "macos"])
def test_other_release_helpers_record_pending_update(tmp_path, monkeypatch, channel):
    monkeypatch.setattr(inst, "staging_root", lambda **kw: tmp_path / "staging")
    monkeypatch.setattr(inst, "launch_detached_helper",
                        lambda argv, *, cwd: SimpleNamespace(pid=77))
    if channel == "portable":
        ctx = _ctx(tmp_path, exe_name="GeminiTranslator-Portable.exe")
        inst.prepare_windows_portable(tmp_path / "staged.exe", ctx)
    else:
        exe = tmp_path / "GeminiTranslator.app" / "Contents" / "MacOS" / "GeminiTranslator"
        exe.parent.mkdir(parents=True)
        exe.write_text("bin")
        ctx = inst.InstallContext(app_pid=1, real_executable=str(exe),
                                  version_label="v10.5.22", expected_version="10.5.22")
        inst.prepare_macos(tmp_path / "u.dmg", ctx)
    assert inst.read_pending_update()["helper_pid"] == 77


def test_pending_update_needs_expected_version(tmp_path, monkeypatch):
    monkeypatch.setattr(inst, "staging_root", lambda **kw: tmp_path)
    ctx = inst.InstallContext(app_pid=1, real_executable="x", version_label="v1")
    inst.record_pending_update(ctx, SimpleNamespace(pid=5))
    assert inst.read_pending_update() is None


_STARTED = 1_800_000_000.0
_APP_EXE = r"C:\Programs\GT\translatorFork_MOD.exe"
_OTHER_EXE = r"D:\old\translatorFork_MOD.exe"


def _record(tmp_path, monkeypatch, attempt_lines=None):
    """Метка установки 10.5.30 из хелпера с PID 999 и его строки попытки."""
    monkeypatch.setattr(inst, "staging_root", lambda **kw: tmp_path / "staging")
    ctx = inst.InstallContext(app_pid=1, real_executable=_APP_EXE,
                              version_label="v10.5.30", expected_version="10.5.30")
    inst.record_pending_update(ctx, SimpleNamespace(pid=999), now=_STARTED)
    if attempt_lines is not None:
        # так пишет Add-Content -Encoding UTF8 из PowerShell: BOM и CRLF
        inst.attempt_log_path("v10.5.30").write_bytes("".join(
            f"[2026-09-23T10:00:00.0000000+03:00] [UPD] {line}\r\n"
            for line in attempt_lines).encode("utf-8-sig"))


def _assess(current="10.5.29", *, after=600, alive=False, **kw):
    return inst.assess_pending_update(
        current, current_executable=_OTHER_EXE, now=_STARTED + after,
        alive=lambda pid: alive and pid == 999, **kw)


def test_assess_pending_update_without_marker(tmp_path, monkeypatch):
    monkeypatch.setattr(inst, "staging_root", lambda **kw: tmp_path)
    assert _assess() is None


def test_assess_pending_update_new_version_started(tmp_path, monkeypatch):
    _record(tmp_path, monkeypatch, ["health ack received; cleaning up"])
    assert _assess("10.5.30").state == "installed"


def test_assess_pending_update_while_helper_works(tmp_path, monkeypatch):
    # Программу открыли заново посреди установки: это не сбой.
    _record(tmp_path, monkeypatch, ["waiting for app pid 1", "running setup"])
    report = _assess(after=60, alive=True)
    assert report.state == "installing"
    assert "10.5.30" in report.text
    assert "Закройте программу" in report.informative


def test_assess_pending_update_distrusts_old_helper_pid(tmp_path, monkeypatch):
    # PID давно завершившегося хелпера мог достаться чужому процессу.
    _record(tmp_path, monkeypatch, ["running setup"])
    assert _assess(after=3 * 3600, alive=True).state == "failed"


@pytest.mark.parametrize("lines, reason", [
    (None, "не запустился"),
    (["running setup",
      "setup failed with exit code 5; setup log C:\\u\\setup-v10.5.30.log",
      "restoring backup"], "код 5"),
    (["new process exited without ack; rolling back", "restoring backup"],
     "закрылась сразу после запуска"),
    (["waiting for app pid 1", "app still running; aborting untouched"],
     "не закрылась"),
    (["creating snapshot backup", "snapshot failed; aborting untouched"],
     "не начиналась"),
    (["running setup"], "прервалась"),
])
def test_assess_pending_update_explains_failure(tmp_path, monkeypatch, lines, reason):
    _record(tmp_path, monkeypatch, lines)
    report = _assess()
    assert report.state == "failed"
    assert "10.5.30" in report.text and "10.5.29" in report.text
    assert reason in report.informative
    assert str(inst.update_log_path()) in report.informative
    if lines:
        assert lines[-1] in report.details  # строки помощника — в подробностях
        assert "[UPD]" not in report.details and "\ufeff" not in report.details


def test_assess_pending_update_points_to_setup_log(tmp_path, monkeypatch):
    _record(tmp_path, monkeypatch,
            ["setup failed with exit code 5; setup log C:\\u\\setup-v10.5.30.log"])
    assert str(inst.setup_log_path("v10.5.30")) in _assess().informative


def test_assess_pending_update_version_mismatch(tmp_path, monkeypatch):
    # Установщик поставил новую версию не туда, откуда запущена программа.
    _record(tmp_path, monkeypatch, [
        "VERSION-MISMATCH: started copy reports 10.5.29, expected 10.5.30; "
        "keeping backup and staged installer"])
    report = _assess(download_url="https://github.com/owner/repo/releases/latest")
    assert report.state == "failed"
    assert r"C:\Programs\GT" in report.informative
    assert "«Пуск»" in report.informative
    # повтор поставил бы версию в ту же чужую папку — не советуем его
    assert "ещё раз" not in report.informative


def test_assess_pending_update_other_copy_running(tmp_path, monkeypatch):
    # Новая версия подтвердила запуск, а пользователь открыл старую копию.
    _record(tmp_path, monkeypatch, ["health ack received; cleaning up"])
    report = _assess()
    assert report.state == "failed"
    assert _APP_EXE in report.informative and _OTHER_EXE in report.informative
    assert "Запускайте" in report.informative  # что делать дальше


def test_assess_pending_update_offers_manual_download(tmp_path, monkeypatch):
    _record(tmp_path, monkeypatch, ["app still running; aborting untouched"])
    url = "https://github.com/owner/repo/releases/latest"
    assert url in _assess(download_url=url).informative


def test_clear_pending_update(tmp_path, monkeypatch):
    _record(tmp_path, monkeypatch)
    inst.clear_pending_update()
    assert inst.read_pending_update() is None
    inst.clear_pending_update()  # повторная очистка — тихий no-op


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-ветка")
def test_pid_alive_posix():
    assert inst.pid_alive(os.getpid())
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    assert not inst.pid_alive(child.pid)
    assert not inst.pid_alive(None)
    assert not inst.pid_alive(0)


class _FakeKernel32:
    """OpenProcess/GetExitCodeProcess/CloseHandle без настоящего Windows."""

    def __init__(self, exit_code, handle=1234):
        self.exit_code = exit_code
        self.handle = handle
        self.closed = []

    def OpenProcess(self, access, inherit, pid):
        return self.handle

    def GetExitCodeProcess(self, handle, code):
        code.value = self.exit_code
        return 1

    def CloseHandle(self, handle):
        self.closed.append(handle)
        return 1


def test_windows_pid_alive_reads_exit_code_and_closes_handle():
    # На Windows os.kill(pid, 0) не проверяет процесс, а убивает его.
    running = _FakeKernel32(exit_code=259)  # STILL_ACTIVE
    assert inst._windows_pid_alive(42, kernel32=running)
    assert running.closed == [1234]
    finished = _FakeKernel32(exit_code=0)
    assert not inst._windows_pid_alive(42, kernel32=finished)
    assert finished.closed == [1234]
    assert not inst._windows_pid_alive(42, kernel32=_FakeKernel32(259, handle=0))


# --- лог ------------------------------------------------------------------

def test_log_update_event_appends(tmp_path, monkeypatch):
    monkeypatch.setattr(inst, "staging_root", lambda **kw: tmp_path)
    inst.log_update_event("проверка записи")
    inst.log_update_event("вторая строка")
    text = (tmp_path / "updater.log").read_text(encoding="utf-8")
    assert "проверка записи" in text and "вторая строка" in text
    assert text.count("[UPD]") == 2
