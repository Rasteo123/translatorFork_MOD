# -*- coding: utf-8 -*-
"""Тесты установочного слоя апдейтера (update_installer.py).

Все операции замены выполняются только во временных каталогах — реальные
исполняемые файлы и бандлы разработчика не трогаются никогда.
"""
import json
import os
import sys
import time
import subprocess
from pathlib import Path

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
    assert str(p) == "/Users/u/Library/Application Support/GeminiTranslator/updater"


def test_staging_root_linux_xdg_and_fallback():
    p = inst.staging_root(platform="linux", env={"XDG_DATA_HOME": "/xdg", "HOME": "/home/u"})
    assert str(p) == "/xdg/GeminiTranslator/updater"
    p2 = inst.staging_root(platform="linux", env={"HOME": "/home/u"})
    assert str(p2) == "/home/u/.local/share/GeminiTranslator/updater"


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


def _installed_script():
    return inst.render_windows_installed_script(
        app_pid=4242,
        setup_path=r"C:\stage\GeminiTranslator-Setup.exe",
        app_dir=r"C:\Program Files\it's",
        backup_dir=r"C:\stage\backup-v10.5.22",
        real_exe=_TRICKY_EXE,
        ack_path=r"C:\stage\ack-v10.5.22.json",
        log_path=r"C:\stage\updater.log",
    )


def _portable_script():
    return inst.render_windows_portable_script(
        app_pid=4242,
        staged_exe=r"C:\stage\GeminiTranslator-Portable.exe",
        real_exe=_TRICKY_EXE,
        ack_path=r"C:\stage\ack-v10.5.22.json",
        log_path=r"C:\stage\updater.log",
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


def test_windows_portable_script_specifics():
    content = _portable_script()
    assert "'.bak'" in content
    assert "/VERYSILENT" not in content
    assert ".rejected" in content


def _ctx(tmp_path, exe_name="translatorFork_MOD.exe"):
    exe = tmp_path / "app" / exe_name
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_bytes(b"MZ")
    return inst.InstallContext(app_pid=os.getpid(), real_executable=str(exe),
                               version_label="v10.5.22")


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
    assert script.exists() and "Wait-Process" in script.read_text(encoding="utf-8")


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


# --- лог ------------------------------------------------------------------

def test_log_update_event_appends(tmp_path, monkeypatch):
    monkeypatch.setattr(inst, "staging_root", lambda **kw: tmp_path)
    inst.log_update_event("проверка записи")
    inst.log_update_event("вторая строка")
    text = (tmp_path / "updater.log").read_text(encoding="utf-8")
    assert "проверка записи" in text and "вторая строка" in text
    assert text.count("[UPD]") == 2
