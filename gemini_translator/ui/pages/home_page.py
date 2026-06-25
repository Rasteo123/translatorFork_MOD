"""Home page of the navigation shell: the tool picker.

Renders the translator tool cards and emits ``tool_selected(tool_id)``. The
shell decides what each id does and pushes the selected tool page.

Each tool is a flat ``QPushButton`` styled as a card (accent icon tile + title
+ description, hero card adds an "Открыть" pill). Child labels are transparent
to mouse events so the whole card is clickable, and ``tool_buttons[tool_id]``
stays a real button (``.click()`` works).
"""
from __future__ import annotations

from PyQt6 import QtCore, QtWidgets
import os
import sys
import subprocess
import requests
import tempfile
from gemini_translator.ui.shell import ShellPage
from gemini_translator.utils.updater import UpdateChecker

# (icon, title, description, tool_id, is_large)
_TOOLS = [
    ("📖", "Переводчик EPUB",
     "Многопоточный перевод книг через Gemini / OpenRouter / GLM с контролем "
     "промпта, глоссария и пакетных задач.",
     "translator", True),
    ("✅", "Валидатор переводов",
     "Вычитка и доработка: текст и HTML бок о бок.",
     "validator", False),
    ("📚", "Менеджер глоссариев",
     "Редактор терминов: AI или ручной режим.",
     "glossary", False),
    ("📝", "EPUB → Rulate MD",
     "Конвертер EPUB в markdown для Rulate.",
     "rulate_export", False),
    ("✂️", "Сплиттер глав",
     "Разбивает большие главы на части.",
     "chapter_splitter", False),
    ("🎧", "Gemini Reader",
     "Озвучивание EPUB через Gemini Live.",
     "gemini_reader", False),
    ("☁️", "RanobeLib Uploader",
     "Загрузчик глав на RanobeLib.",
     "ranobelib_uploader", False),
    ("✏️", "Qidian/Fanqie → Rulate",
     "Черновик книги: данные Qidian/Fanqie + AI-перевод.",
     "qidian_rulate_creator", False),
    ("📊", "Бенчмарк промптов",
     "Сравнение промптов и моделей.",
     "prompt_benchmark", False),
]

_TRANSPARENT = QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents


class _ToolCard(QtWidgets.QFrame):
    """Clickable card: accent icon tile + title + description (+ hero pill).

    A QFrame (sizes to its layout reliably, unlike a QPushButton with child
    widgets) that emits ``clicked`` on left-release; ``click()`` is provided for
    programmatic/test activation.
    """

    clicked = QtCore.pyqtSignal()

    def __init__(self, icon, title, description, is_large, parent=None):
        super().__init__(parent)
        self.setObjectName("toolHeroCard" if is_large else "toolCard")
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)

        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(14, 13, 14, 13)
        row.setSpacing(13)

        tile = QtWidgets.QLabel(icon)
        tile.setObjectName("toolIconTile")
        tile.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        size = 46 if is_large else 38
        tile.setFixedSize(size, size)
        tile.setAttribute(_TRANSPARENT, True)
        row.addWidget(tile, 0, QtCore.Qt.AlignmentFlag.AlignTop)

        text_col = QtWidgets.QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(3)
        title_label = QtWidgets.QLabel(title)
        title_label.setObjectName("toolHeroTitle" if is_large else "toolCardTitle")
        title_label.setAttribute(_TRANSPARENT, True)
        text_col.addWidget(title_label)
        detail_label = QtWidgets.QLabel(description)
        detail_label.setObjectName("toolCardDetail")
        detail_label.setWordWrap(True)
        detail_label.setAttribute(_TRANSPARENT, True)
        text_col.addWidget(detail_label)
        row.addLayout(text_col, 1)

        if is_large:
            open_pill = QtWidgets.QLabel("Открыть")
            open_pill.setObjectName("toolOpenPill")
            open_pill.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            open_pill.setAttribute(_TRANSPARENT, True)
            row.addWidget(open_pill, 0, QtCore.Qt.AlignmentFlag.AlignVCenter)

    def click(self) -> None:
        """Programmatic activation (used by tests and keyboard)."""
        self.clicked.emit()

    def mouseReleaseEvent(self, event):
        if (
            event.button() == QtCore.Qt.MouseButton.LeftButton
            and self.rect().contains(event.position().toPoint())
        ):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class HomePage(ShellPage):
    page_title = ""  # home shows no Back; nav bar title stays empty

    tool_selected = QtCore.pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tool_buttons: dict[str, QtWidgets.QPushButton] = {}
        self._build_ui()
        import os
        if os.environ.get("QT_QPA_PLATFORM") != "offscreen":
            QtCore.QTimer.singleShot(1000, lambda: self.check_for_updates(silent=True))

    def _build_ui(self) -> None:
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(26, 22, 26, 22)
        outer.setSpacing(16)

        top_row = QtWidgets.QHBoxLayout()
        self.btn_check_update = QtWidgets.QPushButton("Проверить обновления")
        self.btn_check_update.setFixedSize(160, 30)
        self.btn_check_update.clicked.connect(lambda: self.check_for_updates(silent=False))
        top_row.addWidget(self.btn_check_update)
        
        from gemini_translator.version import APP_VERSION
        self.lbl_version = QtWidgets.QLabel(f"Текущая версия: {APP_VERSION.lstrip('V ')}")
        top_row.addWidget(self.lbl_version)
        
        top_row.addStretch()
        outer.addLayout(top_row)

        heading = QtWidgets.QLabel("Выберите основной инструмент для запуска")
        heading.setObjectName("homeHeading")
        heading.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(heading)

        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(12)
        small_index = 0
        for icon, title, description, tool_id, is_large in _TOOLS:
            card = _ToolCard(icon, title, description, is_large)
            card.clicked.connect(
                lambda _checked=False, tid=tool_id: self.tool_selected.emit(tid)
            )
            self.tool_buttons[tool_id] = card
            if is_large:
                grid.addWidget(card, 0, 0, 1, 2)
            else:
                row = 1 + small_index // 2
                col = small_index % 2
                small_index += 1
                grid.addWidget(card, row, col)
        outer.addLayout(grid)
        outer.addStretch(1)

    def check_for_updates(self, silent=False):
        self.btn_check_update.setEnabled(False)
        self.btn_check_update.setText("Проверка...")
        self._update_silent = silent
        
        self.updater_thread = UpdateChecker(self)
        self.updater_thread.update_available.connect(self.on_update_available)
        self.updater_thread.no_update.connect(self.on_no_update)
        self.updater_thread.error_occurred.connect(self.on_update_error)
        self.updater_thread.start()
        
    def on_no_update(self):
        self.btn_check_update.setEnabled(True)
        self.btn_check_update.setText("Проверить обновления")
        if getattr(self, '_update_silent', False): return
        QtWidgets.QMessageBox.information(self, "Обновление", "У вас установлена последняя версия программы.")
        
    def on_update_error(self, err):
        self.btn_check_update.setEnabled(True)
        self.btn_check_update.setText("Проверить обновления")
        if getattr(self, '_update_silent', False): return
        QtWidgets.QMessageBox.warning(self, "Ошибка", f"Не удалось проверить обновления: {err}")
        
    def on_update_available(self, version, description, download_url):
        settings = QtCore.QSettings("SiberianTeam", "TranslatorFork")
        ignored = settings.value("updater/ignored_version", "")
        installed = settings.value("updater/installed_version", "")
        
        if getattr(self, '_update_silent', False):
            if version == ignored or version == installed:
                self.btn_check_update.setEnabled(True)
                self.btn_check_update.setText("Проверить обновления")
                return

        self.btn_check_update.setEnabled(True)
        self.btn_check_update.setText("Проверить обновления")
        
        msg = QtWidgets.QMessageBox(self)
        msg.setWindowTitle("Доступно обновление")
        msg.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.LinksAccessibleByMouse)
        
        import re
        html_desc = description.replace('\n', '<br>')
        html_desc = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', html_desc)
        html_desc = re.sub(r'(https?://[^\s<]+)', r'<a href="\1">\1</a>', html_desc)
        
        msg.setTextFormat(QtCore.Qt.TextFormat.RichText)
        msg.setText(f"Доступна новая версия: <b>{version}</b><br><br>{html_desc}")
        
        if download_url == "manual":
            btn_install_now = msg.addButton("Скачать вручную", QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        else:
            btn_install_now = msg.addButton("Скачать и установить", QtWidgets.QMessageBox.ButtonRole.AcceptRole)

        btn_remind_later = msg.addButton("Напомнить позже", QtWidgets.QMessageBox.ButtonRole.RejectRole)
        btn_ignore = msg.addButton("Игнорировать", QtWidgets.QMessageBox.ButtonRole.ActionRole)
        
        msg.exec()
        
        if msg.clickedButton() == btn_ignore:
            settings.setValue("updater/ignored_version", version)
            return
            
        if msg.clickedButton() == btn_remind_later:
            return
        
        install_now = (msg.clickedButton() == btn_install_now)
        
        # Запускаем загрузку
        if version == "source":
            self.download_source_update(install_now)
        elif download_url.startswith("source_zip:"):
            real_url = download_url.split(":", 1)[1]
            self.download_source_zip_update(real_url, install_now, version)
        else:
            self.download_update(download_url, install_now, version)

    def download_source_update(self, install_now):
        if not install_now:
            QtWidgets.QMessageBox.information(self, "Обновление", "Обновление будет установлено вручную (командой git pull).")
            return
            
        progress = QtWidgets.QProgressDialog("Получение обновлений (git pull)...", "Отмена", 0, 100, self)
        progress.setWindowModality(QtCore.Qt.WindowModality.WindowModal)
        progress.setRange(0, 0) # Indeterminate progress
        progress.show()
        
        import subprocess
        import os
        try:
            repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
            QtWidgets.QApplication.processEvents()
            # Используем временные данные пользователя, чтобы git мог сделать авто-мердж (или stash/rebase) при наличии локальных изменений
            result = subprocess.run(
                ["git", "-c", "user.name=Updater", "-c", "user.email=updater@localhost", "pull", "--no-edit"], 
                capture_output=True, text=True, cwd=repo_root
            )
            progress.close()
            
            if result.returncode == 0:
                self.launch_source_updater()
            else:
                QtWidgets.QMessageBox.critical(self, "Ошибка обновления", f"Не удалось обновить исходный код. Возможно, есть локальные изменения или конфликты (блокировки):\n{result.stderr}")
        except Exception as e:
            progress.close()
            QtWidgets.QMessageBox.critical(self, "Ошибка обновления", f"Ошибка: {e}")
            
    def launch_source_updater(self):
        import sys
        from PyQt6.QtCore import QProcess
        from PyQt6.QtWidgets import QApplication
        try:
            self.window().setProperty("is_updating", True)
            QProcess.startDetached(sys.executable, sys.argv)
            QApplication.quit()
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Ошибка перезапуска", f"Не удалось перезапустить программу: {e}")
            
    def download_source_zip_update(self, url, install_now, version):
        if not install_now:
            QtWidgets.QMessageBox.information(self, "Обновление", "Обновление будет установлено вручную.")
            return

        import requests
        import uuid
        import os
        import tempfile

        progress = QtWidgets.QProgressDialog("Загрузка исходного кода...", "Отмена", 0, 100, self)
        progress.setWindowModality(QtCore.Qt.WindowModality.WindowModal)
        progress.show()

        try:
            # GitHub zipballs might redirect, requests handles it
            r = requests.get(url, stream=True, timeout=15)
            # content-length may not be present for GitHub generated zip archives
            total_size = int(r.headers.get('content-length', 0))

            unique_id = uuid.uuid4().hex[:8]
            filename = f"source_update_{unique_id}.zip"
            filepath = os.path.join(tempfile.gettempdir(), filename)

            with open(filepath, 'wb') as f:
                downloaded = 0
                for data in r.iter_content(chunk_size=4096):
                    if progress.wasCanceled():
                        return
                    downloaded += len(data)
                    f.write(data)
                    if total_size:
                        progress.setValue(int(100 * downloaded / total_size))

            # We'll save the version/commit inside the script AFTER successful extraction
            self.launch_source_zip_updater(filepath, version)

        except Exception as e:
            progress.close()
            QtWidgets.QMessageBox.critical(self, "Ошибка загрузки", f"Не удалось скачать обновление: {e}")

    def launch_source_zip_updater(self, filepath, version):
        import subprocess
        import tempfile
        import os
        import sys
        from PyQt6.QtWidgets import QApplication
        
        script_path = os.path.join(tempfile.gettempdir(), "translator_source_updater.py")
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        
        script_content = f'''import sys
import time
import zipfile
import os
import shutil
import subprocess

time.sleep(3) # Wait for the app to close
zip_path = {repr(filepath)}
repo_root = {repr(repo_root)}
main_script = "main.py"
version = {repr(version)}

try:
    with zipfile.ZipFile(zip_path, 'r') as z:
        for member in z.namelist():
            parts = member.split('/', 1)
            if len(parts) > 1 and parts[1]:
                target_path = os.path.join(repo_root, parts[1])
                if member.endswith('/'):
                    os.makedirs(target_path, exist_ok=True)
                else:
                    os.makedirs(os.path.dirname(target_path), exist_ok=True)
                    with z.open(member) as source, open(target_path, 'wb') as target:
                        shutil.copyfileobj(source, target)
    
    # Save the installed version/commit on success
    try:
        from PyQt6.QtCore import QSettings
        settings = QSettings("SiberianTeam", "TranslatorFork")
        if len(version) == 40 and all(c in "0123456789abcdefABCDEF" for c in version):
            settings.setValue("updater/installed_commit", version)
        else:
            settings.setValue("updater/installed_version", version)
        settings.sync()
    except ImportError:
        pass
        
except Exception as e:
    with open(os.path.join(repo_root, "updater_error.log"), "w") as err_log:
        err_log.write("Extraction failed: " + str(e))

kwargs = {{}}
if sys.platform == "win32":
    kwargs["creationflags"] = 0x08000000 # subprocess.CREATE_NO_WINDOW

req_path = os.path.join(repo_root, "requirements.txt")
if os.path.exists(req_path):
    subprocess.call([sys.executable, "-m", "pip", "install", "-r", req_path], **kwargs)

subprocess.Popen([sys.executable, main_script], cwd=repo_root, **kwargs)
'''
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(script_content)

        self.window().setProperty("is_updating", True)
        
        env = dict(os.environ)
        for k in list(env.keys()):
            if k.startswith('_PYI_'):
                del env[k]

        kwargs = {{}}
        if sys.platform == "win32":
            kwargs["creationflags"] = 0x08000000 # subprocess.CREATE_NO_WINDOW

        subprocess.Popen([sys.executable, script_path], cwd=tempfile.gettempdir(), env=env, **kwargs)
        QApplication.quit()

    def download_update(self, url, install_now, version):
        if url == "manual":
            if install_now:
                import webbrowser
                from gemini_translator.api.config import GITHUB_REPO
                webbrowser.open(f"https://github.com/{GITHUB_REPO}/releases/latest")
            else:
                QtWidgets.QMessageBox.information(self, "Обновление", "Пожалуйста, скачайте новую версию исходного кода вручную с GitHub.")
            return

        import requests
        import uuid
        import os
        import tempfile
        
        if not url:
            QtWidgets.QMessageBox.warning(self, "Ошибка", "Ссылка на скачивание не найдена.")
            return
            
        progress = QtWidgets.QProgressDialog("Загрузка обновления...", "Отмена", 0, 100, self)
        progress.setWindowModality(QtCore.Qt.WindowModality.WindowModal)
        progress.show()
        
        try:
            r = requests.get(url, stream=True, timeout=10)
            total_size = int(r.headers.get('content-length', 0))
            
            unique_id = uuid.uuid4().hex[:8]
            filename = f"{unique_id}_{url.split('/')[-1]}"
            filepath = os.path.join(tempfile.gettempdir(), filename)
            
            with open(filepath, 'wb') as f:
                downloaded = 0
                for data in r.iter_content(chunk_size=4096):
                    if progress.wasCanceled():
                        return
                    downloaded += len(data)
                    f.write(data)
                    if total_size:
                        progress.setValue(int(100 * downloaded / total_size))
                        
            progress.setValue(100)
            
            settings = QtCore.QSettings("SiberianTeam", "TranslatorFork")
            settings.setValue("updater/installed_version", version)
            settings.sync()
            
            if install_now:
                self.launch_updater(filepath)
            else:
                QtWidgets.QMessageBox.information(self, "Успех", "Обновление скачано и будет установлено при следующем запуске (или вручную).")
                
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Ошибка загрузки", f"Не удалось скачать обновление: {e}")

    @staticmethod
    def _get_real_executable():
        """Get the real on-disk path to the application executable.

        In PyInstaller ``--onefile`` builds ``sys.executable`` points to the
        bootstrap binary inside a temporary ``_MEI*`` directory.  That folder
        is removed when the process exits, so using it for a *restart* command
        causes "Failed to load Python DLL" on Windows.

        This helper resolves the original launch path instead.
        """
        import re
        if getattr(sys, 'frozen', False):
            exe = os.path.abspath(sys.executable)
            # On Windows onefile builds sys.executable may resolve inside
            # the _MEI* temp dir.  Fall back to sys.argv[0] which always
            # holds the real exe the user double-clicked.
            if sys.platform == 'win32' and re.search(r'_MEI\d+', exe):
                exe = os.path.abspath(sys.argv[0])
            return exe
        return os.path.abspath(sys.executable)

    def launch_updater(self, filepath):
        import subprocess
        import tempfile
        import os
        import sys
        import copy
        
        # Prepare a clean environment for PyInstaller restart
        env = copy.deepcopy(os.environ)
        env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
        for k in list(env.keys()):
            if k.startswith('_PYI_'):
                del env[k]

        if sys.platform == "win32":
            log_path = os.path.join(tempfile.gettempdir(), "translator_updater.log")
            real_exe = self._get_real_executable()
            if "setup" in filepath.lower() or "install" in filepath.lower():
                # Inno Setup with skipifsilent flag does not restart the app
                # automatically, so we must start it explicitly here.
                bat_content = f"""@echo off
chcp 65001 >nul
set PYINSTALLER_RESET_ENVIRONMENT=1
echo [%date% %time%] Waiting for application to close... >> "{log_path}"
timeout /t 3 /nobreak >nul
echo [%date% %time%] Running installer... >> "{log_path}"
start /wait "" "{filepath}" /VERYSILENT /SUPPRESSMSGBOXES /FORCECLOSEAPPLICATIONS >> "{log_path}" 2>&1
echo [%date% %time%] Restarting application... >> "{log_path}"
start "" "{real_exe}"
echo [%date% %time%] Installer finished. >> "{log_path}"
del "%~f0"
"""
            else:
                bat_content = f"""@echo off
chcp 65001 >nul
set PYINSTALLER_RESET_ENVIRONMENT=1
echo [%date% %time%] Waiting for application to close... >> "{log_path}"
timeout /t 3 /nobreak >nul
echo [%date% %time%] Replacing portable executable... >> "{log_path}"
copy /Y "{filepath}" "{real_exe}" >> "{log_path}" 2>&1
if errorlevel 1 (
    echo [%date% %time%] Retry copy... >> "{log_path}"
    timeout /t 2 /nobreak >nul
    copy /Y "{filepath}" "{real_exe}" >> "{log_path}" 2>&1
)
echo [%date% %time%] Restarting application... >> "{log_path}"
start "" "{real_exe}"
del "%~f0"
"""
            bat_path = os.path.join(tempfile.gettempdir(), "translator_updater.bat")
            with open(bat_path, "w", encoding="utf-8") as f:
                f.write(bat_content)
                
            try:
                subprocess.call(['powershell', '-Command', f"Unblock-File -LiteralPath '{filepath}'"])
            except Exception:
                pass
            subprocess.Popen(["cmd.exe", "/c", bat_path], creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0, env=env)
            
        elif sys.platform == "darwin" and (filepath.endswith('.dmg') or filepath.endswith('.zip')):
            app_bundle_path = sys.executable
            while app_bundle_path != '/' and not app_bundle_path.endswith('.app'):
                app_bundle_path = os.path.dirname(app_bundle_path)
                
            if app_bundle_path.endswith('.app'):
                sh_content = f"""#!/bin/bash
# Clear PyInstaller environment variables to prevent crashes in the new app
unset DYLD_LIBRARY_PATH
unset LD_LIBRARY_PATH
export PYINSTALLER_RESET_ENVIRONMENT=1

# Relaxed error handling — individual commands may return non-zero
# legitimately (e.g. hdiutil, xattr).
exec >> /tmp/updater.log 2>&1
echo "[$(date)] Starting update..."
sleep 5

if [[ "{filepath}" == *.dmg ]]; then
    MNT_OUTPUT=$(hdiutil attach -nobrowse "{filepath}" 2>&1 | grep '/Volumes/' || true)
    MNT=$(echo "$MNT_OUTPUT" | awk -F '/Volumes/' '{{print "/Volumes/"$2}}' | xargs)
    if [ -n "$MNT" ]; then
        NEW_APP=$(find "$MNT" -name "*.app" -maxdepth 1 | head -n 1)
        if [ -n "$NEW_APP" ]; then
            echo "[$(date)] Found new app in DMG: $NEW_APP"
            if [ -d "{app_bundle_path}.old" ]; then
                rm -rf "{app_bundle_path}.old"
            fi
            mv "{app_bundle_path}" "{app_bundle_path}.old"
            ditto "$NEW_APP" "{app_bundle_path}"
            xattr -cr "{app_bundle_path}" || true
            echo "[$(date)] Update successful"
        fi
        hdiutil detach "$MNT" -force || true
    fi
elif [[ "{filepath}" == *.zip ]]; then
    EXTRACT_DIR=$(mktemp -d)
    unzip -q -o "{filepath}" -d "$EXTRACT_DIR"
    NEW_APP=$(find "$EXTRACT_DIR" -name "*.app" -maxdepth 2 | head -n 1)
    if [ -n "$NEW_APP" ]; then
        echo "[$(date)] Found new app in ZIP: $NEW_APP"
        if [ -d "{app_bundle_path}.old" ]; then
            rm -rf "{app_bundle_path}.old"
        fi
        mv "{app_bundle_path}" "{app_bundle_path}.old"
        ditto "$NEW_APP" "{app_bundle_path}"
        xattr -cr "{app_bundle_path}" || true
        echo "[$(date)] Update successful"
    fi
    rm -rf "$EXTRACT_DIR"
fi

sleep 1
open "{app_bundle_path}"
rm -f "$0"
"""
                sh_path = os.path.join(tempfile.gettempdir(), "translator_updater.sh")
                with open(sh_path, "w", encoding="utf-8") as f:
                    f.write(sh_content)
                os.chmod(sh_path, 0o755)
                subprocess.Popen(
                    ["/bin/bash", sh_path],
                    start_new_session=True,
                    env=env
                )
            else:
                subprocess.Popen(['open', filepath], env=env)
        else:
            if sys.platform == "darwin":
                subprocess.Popen(['open', filepath], env=env)
            else:
                subprocess.Popen([filepath], env=env)
                
        # Force quit to avoid "Are you sure you want to quit?" dialogs
        os._exit(0)
