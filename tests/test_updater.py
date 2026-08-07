import sys
import os
import pytest
from unittest.mock import patch, MagicMock, PropertyMock
from PyQt6 import QtWidgets, QtCore
from gemini_translator.utils.updater import UpdateChecker
from gemini_translator.ui.pages.home_page import HomePage

def test_update_checker_finds_update(qtbot):
    with patch('requests.get') as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "tag_name": "v99.99.99",
            "body": "New release!",
            "assets": [
                {"name": "GeminiTranslator-Setup.exe", "browser_download_url": "http://example.com/setup.exe"},
                {"name": "GeminiTranslator.dmg", "browser_download_url": "http://example.com/mac.dmg"}
            ]
        }
        mock_get.return_value = mock_response
        
        with patch('gemini_translator.utils.updater.APP_VERSION', '1.0.0'):
            with patch.object(UpdateChecker, 'is_source_mode', return_value=False):
                with patch('sys.frozen', True, create=True):
                    checker = UpdateChecker()
                    with qtbot.waitSignal(checker.update_available, timeout=1000) as blocker:
                        checker.run()
                
            assert blocker.args[0] == "99.99.99"
            assert blocker.args[1] == "New release!"
            if sys.platform == "win32":
                assert blocker.args[2] == "http://example.com/setup.exe"
            elif sys.platform == "darwin":
                assert blocker.args[2] == "http://example.com/mac.dmg"

def test_update_checker_source_no_git_finds_zip(qtbot):
    with patch('requests.get') as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "sha": "1234567890abcdef1234567890abcdef12345678",
            "commit": {
                "message": "Update something"
            }
        }
        mock_get.return_value = mock_response
        
        with patch('PyQt6.QtCore.QSettings.value', return_value="old_sha"):
            with patch('gemini_translator.utils.updater.UpdateChecker.is_source_mode', return_value=False):
                with patch('sys.frozen', False, create=True):
                    checker = UpdateChecker()
                    with qtbot.waitSignal(checker.update_available, timeout=1000) as blocker:
                        checker.run()
                
            assert blocker.args[0] == "1234567890abcdef1234567890abcdef12345678"
            assert "Update something" in blocker.args[1]
            assert "source_zip:" in blocker.args[2]

def test_update_checker_no_update(qtbot):
    with patch('requests.get') as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "tag_name": "v1.0.0",
            "body": "Old release!",
            "assets": []
        }
        mock_get.return_value = mock_response
        
        with patch('gemini_translator.utils.updater.APP_VERSION', '2.0.0'):
            with patch.object(UpdateChecker, 'is_source_mode', return_value=False):
                checker = UpdateChecker()
                with qtbot.waitSignal(checker.no_update, timeout=1000):
                    checker.run()

def test_show_update_dialog_buttons(qtbot, qapp):
    home_page = HomePage()
    qtbot.addWidget(home_page)
    
    # We patch QMessageBox to intercept the created buttons
    original_init = QtWidgets.QMessageBox.__init__
    
    dialog_buttons = []
    def mock_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        # Monkey patch addButton to record buttons
        original_add = self.addButton
        def mock_add(*args_add, **kwargs_add):
            if isinstance(args_add[0], str):
                dialog_buttons.append(args_add[0])
            return original_add(*args_add, **kwargs_add)
        self.addButton = mock_add
        
        # Monkey patch exec to just return without blocking
        self.exec = MagicMock(return_value=0)
        self.clickedButton = MagicMock(return_value=None)
        
    with patch.object(QtWidgets.QMessageBox, '__init__', mock_init):
        # Prevent actual download by patching download_update
        with patch.object(home_page, 'download_update'):
            home_page.on_update_available("99.99.99", "Desc", "url")
            
    # The button "Установить при следующем запуске" should NOT be present
    assert "Скачать и установить" in dialog_buttons
    assert "Напомнить позже" in dialog_buttons
    assert "Игнорировать" in dialog_buttons
    assert "Скачать сейчас и установить при следующем запуске приложения" not in dialog_buttons

def test_launch_updater_windows_installer(qtbot, tmp_path):
    import tempfile
    
    home_page = HomePage()
    filepath = str(tmp_path / "GeminiTranslator-Setup.exe")
    
    with patch('sys.platform', 'win32'):
        with patch('subprocess.call'), patch('subprocess.Popen'), patch('os._exit'):
            with patch.object(HomePage, '_get_real_executable', return_value=r'C:\Program Files\GeminiTranslator\translatorFork_MOD.exe'):
                home_page.launch_updater(filepath)
                
                bat_path = os.path.join(tempfile.gettempdir(), "translator_updater.bat")
                assert os.path.exists(bat_path)
                
                with open(bat_path, "r", encoding="utf-8") as f:
                    content = f.read()
                    
                assert "translator_updater.log" in content
                assert "/VERYSILENT" in content
                assert "start /wait" in content
                # Installer variant now explicitly restarts the app
                lines = content.strip().splitlines()
                start_lines = [l for l in lines if l.strip().startswith('start ')]
                assert len(start_lines) == 2

def test_launch_updater_windows_installer_restarts(qtbot, tmp_path):
    """Verify installer bat DOES contain a restart command.

    Inno Setup ``[Run]`` has skipifsilent flag, so we must restart the app
    after ``/VERYSILENT`` installation.
    """
    import tempfile
    
    home_page = HomePage()
    filepath = str(tmp_path / "GeminiTranslator-Setup.exe")
    real_exe = r'C:\fake\translatorFork_MOD.exe'
    
    with patch('sys.platform', 'win32'):
        with patch('subprocess.call'), patch('subprocess.Popen'), patch('os._exit'):
            with patch.object(HomePage, '_get_real_executable', return_value=real_exe):
                home_page.launch_updater(filepath)
                
                bat_path = os.path.join(tempfile.gettempdir(), "translator_updater.bat")
                with open(bat_path, "r", encoding="utf-8") as f:
                    content = f.read()
                
                lines = content.strip().splitlines()
                restart_lines = [l for l in lines if l.strip().startswith('start ""') and '/wait' not in l]
                assert len(restart_lines) == 1
                assert real_exe in restart_lines[0]

def test_launch_updater_windows_portable(qtbot, tmp_path):
    import tempfile
    
    home_page = HomePage()
    filepath = str(tmp_path / "translatorFork_MOD.exe")
    
    with patch('sys.platform', 'win32'):
        with patch('subprocess.call'), patch('subprocess.Popen'), patch('os._exit'):
            with patch.object(HomePage, '_get_real_executable', return_value=r'C:\Users\test\translatorFork_MOD.exe'):
                home_page.launch_updater(filepath)
                
                bat_path = os.path.join(tempfile.gettempdir(), "translator_updater.bat")
                assert os.path.exists(bat_path)
                
                with open(bat_path, "r", encoding="utf-8") as f:
                    content = f.read()
                    
                assert "translator_updater.log" in content
                assert "/VERYSILENT" not in content
                assert "copy /Y" in content
                # Portable variant must use the real exe path, not _MEI temp
                assert r'C:\Users\test\translatorFork_MOD.exe' in content

def test_launch_updater_windows_portable_uses_real_exe(qtbot, tmp_path):
    """Portable bat must copy the downloaded file to the real exe path
    and restart from that real path — not from ``sys.executable``
    which may point to a PyInstaller ``_MEI*`` temp directory."""
    import tempfile
    
    home_page = HomePage()
    filepath = str(tmp_path / "translatorFork_MOD.exe")
    real_exe = r'D:\Apps\GeminiTranslator\translatorFork_MOD.exe'
    
    with patch('sys.platform', 'win32'):
        with patch('subprocess.call'), patch('subprocess.Popen'), patch('os._exit'):
            with patch.object(HomePage, '_get_real_executable', return_value=real_exe):
                home_page.launch_updater(filepath)
                
                bat_path = os.path.join(tempfile.gettempdir(), "translator_updater.bat")
                with open(bat_path, "r", encoding="utf-8") as f:
                    content = f.read()
                    
                # Both copy targets and restart must use the real path
                assert content.count(real_exe) >= 3, (
                    f"Expected real_exe path at least 3 times (2 copies + 1 start), "
                    f"found {content.count(real_exe)}"
                )

def test_launch_updater_macos(qtbot, tmp_path):
    import tempfile
    
    home_page = HomePage()
    filepath = str(tmp_path / "GeminiTranslator.dmg")
    
    with patch('sys.platform', 'darwin'):
        with patch('sys.executable', '/Applications/GeminiTranslator.app/Contents/MacOS/GeminiTranslator'):
            with patch('subprocess.call'), patch('subprocess.Popen') as mock_popen, patch('os._exit'), patch('os.chmod') as mock_chmod:
                home_page.launch_updater(filepath)
                
                sh_path = os.path.join(tempfile.gettempdir(), "translator_updater.sh")
                assert os.path.exists(sh_path)
                mock_chmod.assert_called_once_with(sh_path, 0o700)
                
                with open(sh_path, "r", encoding="utf-8") as f:
                    content = f.read()
                    
                # Strict flags must NOT be present — they cause early abort
                assert "set -euo pipefail" not in content
                assert "unset DYLD_LIBRARY_PATH" in content
                assert "unset LD_LIBRARY_PATH" in content
                assert "updater.log" in content
                assert ".old" in content
                assert f'rm -rf "/Applications/GeminiTranslator.app"' not in content
                assert "ditto" in content
                assert "chmod +x" not in content
                # Must use plain `open` without -n flag
                assert "open -n" not in content
                assert 'open "/Applications/GeminiTranslator.app"' in content

def test_launch_updater_macos_detached(qtbot, tmp_path):
    """macOS updater script must be launched in a new session so it
    survives the parent ``os._exit(0)``."""
    import tempfile
    
    home_page = HomePage()
    filepath = str(tmp_path / "GeminiTranslator.dmg")
    
    with patch('sys.platform', 'darwin'):
        with patch('sys.executable', '/Applications/GeminiTranslator.app/Contents/MacOS/GeminiTranslator'):
            with patch('subprocess.call'), patch('subprocess.Popen') as mock_popen, patch('os._exit'), patch('os.chmod'):
                home_page.launch_updater(filepath)
                
                # Popen must be called with start_new_session=True
                popen_calls = [c for c in mock_popen.call_args_list if '/bin/bash' in str(c)]
                assert len(popen_calls) == 1
                _, kwargs = popen_calls[0]
                assert kwargs.get('start_new_session') is True, \
                    "macOS updater Popen must use start_new_session=True"


# --- Tests for _get_real_executable ---

def test_get_real_executable_not_frozen():
    """When not running as a PyInstaller bundle, returns sys.executable."""
    with patch('sys.executable', '/usr/bin/python3'):
        with patch.object(sys, 'frozen', False, create=True):
            result = HomePage._get_real_executable()
            assert result == os.path.abspath('/usr/bin/python3')

def test_get_real_executable_frozen_normal():
    """Frozen build where sys.executable is NOT in _MEI temp dir."""
    with patch('sys.executable', r'C:\Program Files\App\myapp.exe'):
        with patch.object(sys, 'frozen', True, create=True):
            with patch('sys.platform', 'win32'):
                result = HomePage._get_real_executable()
                assert '_MEI' not in result
                assert 'myapp.exe' in result

def test_get_real_executable_frozen_mei_fallback():
    """Frozen build where sys.executable points to _MEI temp dir.
    
    Must fall back to sys.argv[0] which holds the real launch path.
    """
    mei_path = r'C:\Users\Admin\AppData\Local\Temp\_MEI140642\translatorFork_MOD.exe'
    real_path = r'C:\Users\Admin\Desktop\translatorFork_MOD.exe'
    
    with patch('sys.executable', mei_path):
        with patch.object(sys, 'frozen', True, create=True):
            with patch('sys.platform', 'win32'):
                with patch('sys.argv', [real_path]):
                    result = HomePage._get_real_executable()
                    assert '_MEI' not in result
                    assert result == os.path.abspath(real_path)

def test_get_real_executable_frozen_darwin():
    """On macOS frozen builds, sys.executable is inside the .app bundle
    and does NOT contain _MEI, so it should be returned as-is."""
    mac_exe = '/Applications/GeminiTranslator.app/Contents/MacOS/GeminiTranslator'
    
    with patch('sys.executable', mac_exe):
        with patch.object(sys, 'frozen', True, create=True):
            with patch('sys.platform', 'darwin'):
                result = HomePage._get_real_executable()
                assert result == os.path.abspath(mac_exe)

def test_update_checker_source_mode(monkeypatch, qtbot):
    from gemini_translator.utils.updater import UpdateChecker
    import subprocess
    import os
    
    # Mock sys.frozen = False
    import sys
    monkeypatch.setattr(sys, 'frozen', False, raising=False)
    
    # Mock os.path.exists to simulate .git dir
    orig_exists = os.path.exists
    def mock_exists(path):
        if path.endswith('.git'): return True
        return orig_exists(path)
    monkeypatch.setattr(os.path, 'exists', mock_exists)
    
    # Mock subprocess.run for git fetch and rev-list
    class MockResult:
        def __init__(self, stdout, returncode=0):
            self.stdout = stdout
            self.returncode = returncode
            
    def mock_run(cmd, *args, **kwargs):
        if 'fetch' in cmd:
            return MockResult(b"")
        if 'rev-list' in cmd:
            return MockResult(b"1\n") # 1 new commit
        return MockResult(b"")
        
    monkeypatch.setattr(subprocess, 'run', mock_run)
    
    checker = UpdateChecker()
    with qtbot.waitSignal(checker.update_available, timeout=1000) as blocker:
        checker.run()
        
    assert blocker.args[0] == "source"
    assert "доступны обновления" in blocker.args[1].lower()
    assert blocker.args[2] == ""


def test_pick_platform_asset_prefers_setup_exe_over_portable():
    """В релизе теперь два .exe: Setup и Portable. Автообновление должно
    ставить инсталлер, а не скачивать портативный exe."""
    from gemini_translator.utils.updater import _pick_platform_asset

    assets = [
        {"name": "GeminiTranslator-Portable.exe", "browser_download_url": "http://example.com/portable.exe"},
        {"name": "GeminiTranslator-Setup.exe", "browser_download_url": "http://example.com/setup.exe"},
        {"name": "GeminiTranslator-macOS.dmg", "browser_download_url": "http://example.com/mac.dmg"},
    ]

    assert _pick_platform_asset(assets, "win32") == "http://example.com/setup.exe"


def test_pick_platform_asset_falls_back_to_any_exe():
    from gemini_translator.utils.updater import _pick_platform_asset

    assets = [
        {"name": "GeminiTranslator-Portable.exe", "browser_download_url": "http://example.com/portable.exe"},
    ]

    assert _pick_platform_asset(assets, "win32") == "http://example.com/portable.exe"


def test_pick_platform_asset_darwin_prefers_dmg_then_zip():
    from gemini_translator.utils.updater import _pick_platform_asset

    assets = [
        {"name": "GeminiTranslator-macOS.zip", "browser_download_url": "http://example.com/mac.zip"},
        {"name": "GeminiTranslator-macOS.dmg", "browser_download_url": "http://example.com/mac.dmg"},
    ]

    assert _pick_platform_asset(assets, "darwin") == "http://example.com/mac.dmg"
    assert _pick_platform_asset(assets[:1], "darwin") == "http://example.com/mac.zip"


def test_pick_platform_asset_unknown_platform_takes_first():
    from gemini_translator.utils.updater import _pick_platform_asset

    assets = [
        {"name": "whatever.bin", "browser_download_url": "http://example.com/x.bin"},
    ]

    assert _pick_platform_asset(assets, "linux") == "http://example.com/x.bin"
    assert _pick_platform_asset([], "win32") == ""
