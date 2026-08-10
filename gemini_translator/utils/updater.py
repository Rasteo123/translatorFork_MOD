# -*- coding: utf-8 -*-
import sys
import os
import re
import json
import enum
import subprocess
from dataclasses import dataclass
from pathlib import Path

import requests
from PyQt6.QtCore import QThread, pyqtSignal
from gemini_translator.api import config as api_config
from gemini_translator.api.config import GITHUB_REPO
from gemini_translator.version import APP_VERSION


# --- Версии релизов -------------------------------------------------------

class UpdateError(Exception):
    """Ошибка апдейтера, пригодная для показа пользователю."""

    @property
    def user_message(self) -> str:
        return str(self)


_VERSION_RE = re.compile(
    r"^[vV]?\s*(\d+)\.(\d+)\.(\d+)"
    r"(?:-(?P<kind>hotfix|rc|beta|alpha)\.?(?P<num>\d+))?$"
)


@dataclass(frozen=True, order=True)
class ReleaseVersion:
    """Версия релиза с корректным порядком: prerelease < final < hotfix."""

    release: tuple
    phase: int  # 0 = prerelease, 1 = final, 2 = post (легаси-hotfix)
    phase_num: int

    @property
    def is_final(self) -> bool:
        return self.phase == 1


def parse_version_tag(text) -> ReleaseVersion:
    """Разбирает тег/версию; непонятный формат — ошибка, а не «нет обновлений»."""
    if not isinstance(text, str):
        raise UpdateError(f"Некорректная версия: {text!r}")
    m = _VERSION_RE.match(text.strip())
    if m is None:
        raise UpdateError(f"Некорректная версия: {text!r}")
    release = tuple(int(g) for g in m.group(1, 2, 3))
    kind, num = m.group("kind"), m.group("num")
    if kind is None:
        return ReleaseVersion(release, 1, 0)
    if kind == "hotfix":
        return ReleaseVersion(release, 2, int(num))
    return ReleaseVersion(release, 0, int(num))


# --- Идентичность сборки и канал обновления -------------------------------

BUILD_IDENTITY_FILENAME = "update-build.json"
ARCHIVE_IDENTITY_FILENAME = ".translator-update.json"

_HEX40_RE = re.compile(r"^[0-9a-fA-F]{40}$")


class UpdateChannel(enum.Enum):
    WINDOWS_INSTALLED = "windows-installed"
    WINDOWS_PORTABLE = "windows-portable"
    MACOS = "macos"
    SOURCE_GIT = "source-git"
    SOURCE_ARCHIVE = "source-archive"
    DEVELOPMENT = "development"


class UpdateState(enum.Enum):
    IDLE = "idle"
    CHECKING = "checking"
    DOWNLOADING = "downloading"
    VERIFYING = "verifying"
    PREPARING = "preparing"
    EXITING = "exiting"


@dataclass(frozen=True)
class BuildIdentity:
    version: ReleaseVersion
    tag: str
    commit: str


def project_root() -> Path:
    """Корень проекта, вычисленный от этого файла — не от текущего каталога."""
    return Path(__file__).resolve().parents[2]


def read_build_identity():
    """Читает встроенный update-build.json; любая невалидность — None.

    Замороженная сборка без валидной идентичности — DEVELOPMENT: она никогда
    не обновляет себя автоматически.
    """
    try:
        path = Path(api_config.get_resource_path(BUILD_IDENTITY_FILENAME))
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or data.get("schema") != 1:
            return None
        version = parse_version_tag(str(data["version"]))
        tag = str(data["tag"])
        commit = str(data["commit"])
        if not version.is_final:
            return None
        if tag != f"v{data['version']}":
            return None
        if not _HEX40_RE.match(commit):
            return None
        return BuildIdentity(version, tag, commit)
    except Exception:
        return None


def read_archive_identity(root):
    """Читает локальный .translator-update.json архивной установки.

    Возвращает dict {"schema":1,"commit":<40hex>,"files":[...]} или None,
    если идентичности нет или ей нельзя доверять.
    """
    try:
        with open(Path(root) / ARCHIVE_IDENTITY_FILENAME, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or data.get("schema") != 1:
            return None
        if not _HEX40_RE.match(str(data.get("commit", ""))):
            return None
        files = data.get("files")
        if files is not None and not (
            isinstance(files, list) and all(isinstance(x, str) for x in files)
        ):
            return None
        return data
    except Exception:
        return None


def detect_update_channel() -> UpdateChannel:
    """Определяет канал по идентичности исполняемого файла, не по CWD.

    Переименованный exe осознанно деградирует до DEVELOPMENT (fail-closed).
    """
    if getattr(sys, "frozen", False):
        if read_build_identity() is None:
            return UpdateChannel.DEVELOPMENT
        norm_path = sys.executable.replace("\\", "/")
        exe = norm_path.rsplit("/", 1)[-1].lower()
        if exe == "geminitranslator-portable.exe":
            return UpdateChannel.WINDOWS_PORTABLE
        if exe == "translatorfork_mod.exe":
            return UpdateChannel.WINDOWS_INSTALLED
        if "GeminiTranslator.app/Contents/MacOS" in norm_path:
            return UpdateChannel.MACOS
        return UpdateChannel.DEVELOPMENT
    if (project_root() / ".git").exists():
        return UpdateChannel.SOURCE_GIT
    return UpdateChannel.SOURCE_ARCHIVE


def _pick_platform_asset(assets, platform) -> str:
    """Выбирает ссылку на подходящий ассет релиза для платформы.

    В релизе лежат и инсталлер (Setup.exe), и портативный exe: для
    автообновления на Windows предпочитаем инсталлер. На macOS — dmg,
    затем zip. Если ничего не подошло — первый ассет.
    """
    def _url(asset):
        return asset["browser_download_url"]

    if platform == "win32":
        exe_assets = [a for a in assets if a["name"].lower().endswith(".exe")]
        setup_assets = [a for a in exe_assets if "setup" in a["name"].lower()]
        if setup_assets:
            return _url(setup_assets[0])
        if exe_assets:
            return _url(exe_assets[0])
    elif platform == "darwin":
        dmg_url = None
        zip_url = None
        for asset in assets:
            name = asset["name"].lower()
            if name.endswith(".dmg") and dmg_url is None:
                dmg_url = _url(asset)
            elif name.endswith(".zip") and zip_url is None:
                zip_url = _url(asset)
        if dmg_url or zip_url:
            return dmg_url or zip_url

    return _url(assets[0]) if assets else ""


class UpdateChecker(QThread):
    update_available = pyqtSignal(str, str, str) # version, description, download_url
    error_occurred = pyqtSignal(str)
    no_update = pyqtSignal()

    def is_source_mode(self):
        import sys, os
        is_frozen = getattr(sys, 'frozen', False)
        # Check if we are running in a git repo
        git_dir = os.path.join(os.path.abspath(os.path.dirname(__file__)), "..", "..", ".git")
        return not is_frozen and (os.path.exists('.git') or os.path.exists(git_dir))

    def run(self):
        import sys
        if getattr(sys, 'frozen', False):
            self._check_release_update()
        elif self.is_source_mode():
            self._check_source_update()
        else:
            # Source mode but without git (e.g. downloaded zip)
            self._check_commit_update()

    def _check_source_update(self):
        try:
            repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
            fetch_res = subprocess.run(["git", "fetch"], capture_output=True, text=True, cwd=repo_root)
            if fetch_res.returncode != 0:
                self.error_occurred.emit("Ошибка при выполнении git fetch")
                return
                
            rev_res = subprocess.run(["git", "rev-list", "--count", "HEAD..@{u}"], capture_output=True, text=True, cwd=repo_root)
            if rev_res.returncode == 0:
                count = int(rev_res.stdout.strip() or "0")
                if count > 0:
                    self.update_available.emit("source", f"Доступны обновления на GitHub ({count} новых коммитов).", "")
                else:
                    self.no_update.emit()
            else:
                self.no_update.emit()
        except Exception as e:
            self.error_occurred.emit(str(e))
            
    def _check_commit_update(self):
        try:
            from PyQt6.QtCore import QSettings
            url = f"https://api.github.com/repos/{GITHUB_REPO}/commits/main"
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                data = response.json()
                latest_sha = data.get("sha")
                if not latest_sha:
                    self.no_update.emit()
                    return
                
                settings = QSettings("SiberianTeam", "TranslatorFork")
                installed_commit = settings.value("updater/installed_commit", "")
                
                if not installed_commit:
                    # First run from source ZIP, silently save the commit to track future updates
                    settings.setValue("updater/installed_commit", latest_sha)
                    settings.sync()
                    self.no_update.emit()
                    return
                
                if installed_commit != latest_sha:
                    commit_msg = data.get("commit", {}).get("message", "Доступно обновление исходного кода.")
                    body = f"Найден новый коммит:\n{commit_msg}"
                    zip_url = f"https://api.github.com/repos/{GITHUB_REPO}/zipball/main"
                    self.update_available.emit(latest_sha, body, f"source_zip:{zip_url}")
                else:
                    self.no_update.emit()
            else:
                self.error_occurred.emit(f"Ошибка API GitHub: {response.status_code}")
        except Exception as e:
            self.error_occurred.emit(str(e))

    def _check_release_update(self):
        try:
            url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
            # Disable verify=False if possible, but keep simple timeout
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                data = response.json()
                latest_version = data.get("tag_name", "").lstrip("v")
                current_version = APP_VERSION.lstrip("v")
                
                # Basic version comparison
                import re
                def parse_version(v):
                    return [int(x) for x in re.findall(r'\d+', v)]
                
                latest_parsed = parse_version(latest_version)
                current_parsed = parse_version(current_version)
                
                if latest_parsed > current_parsed:
                    body = data.get("body", "Доступно новое обновление.")

                    download_url = _pick_platform_asset(
                        data.get("assets", []), sys.platform)

                    self.update_available.emit(latest_version, body, download_url)
                else:
                    self.no_update.emit()
            else:
                self.error_occurred.emit(f"HTTP {response.status_code}")
        except Exception as e:
            self.error_occurred.emit(str(e))
