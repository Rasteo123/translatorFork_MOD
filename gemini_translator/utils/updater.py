# -*- coding: utf-8 -*-
import sys
import os
import re
import json
import enum
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

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


# --- Манифест релиза ------------------------------------------------------

UPDATE_MANIFEST_FILENAME = "update-manifest.json"

_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    url: str
    platform: str
    channel: str
    size: int
    sha256: str  # None для архивов без опубликованного хеша


@dataclass(frozen=True)
class UpdateManifest:
    version: ReleaseVersion
    tag: str
    commit: str
    assets: tuple


def parse_update_manifest(data, release_tag, gh_assets) -> UpdateManifest:
    """Валидирует манифест и связывает его записи с ассетами GitHub-релиза.

    Каждая запись манифеста обязана однозначно совпасть по имени с загруженным
    ассетом релиза — иначе релиз битый и это ошибка, а не «нет обновлений».
    """
    if not isinstance(data, dict):
        raise UpdateError("Манифест обновления повреждён (не объект)")
    if data.get("schema") != 1:
        raise UpdateError(f"Неизвестная схема манифеста: {data.get('schema')!r}")
    tag = str(data.get("tag", ""))
    if tag != release_tag:
        raise UpdateError(f"Тег манифеста {tag!r} не совпадает с тегом релиза {release_tag!r}")
    version = parse_version_tag(str(data.get("version", "")))
    if tag != f"v{data.get('version')}":
        raise UpdateError("Версия и тег в манифесте не согласованы")
    commit = str(data.get("commit", ""))
    if not _HEX40_RE.match(commit):
        raise UpdateError("Некорректный commit в манифесте")
    raw_assets = data.get("assets")
    if not isinstance(raw_assets, list) or not raw_assets:
        raise UpdateError("Манифест не содержит списка ассетов")
    by_name = {}
    for gh in gh_assets or []:
        name = gh.get("name")
        url = gh.get("browser_download_url")
        if name and url:
            by_name[name] = url
    assets = []
    for entry in raw_assets:
        if not isinstance(entry, dict):
            raise UpdateError("Запись ассета в манифесте повреждена")
        name = str(entry.get("name", ""))
        channel = str(entry.get("channel", ""))
        platform = str(entry.get("platform", ""))
        size = entry.get("size")
        sha256 = str(entry.get("sha256", "")).lower()
        if not name or not channel or not platform:
            raise UpdateError(f"Неполная запись ассета: {name!r}")
        if not isinstance(size, int) or size <= 0:
            raise UpdateError(f"Некорректный размер ассета {name!r}")
        if not _HEX64_RE.match(sha256):
            raise UpdateError(f"Некорректный SHA-256 ассета {name!r}")
        url = by_name.get(name)
        if not url:
            raise UpdateError(f"Ассет {name!r} из манифеста не загружен в релиз")
        assets.append(ReleaseAsset(name, url, platform, channel, size, sha256))
    return UpdateManifest(version, tag, commit, tuple(assets))


def select_release_asset(manifest, channel):
    """Выбирает ассет строго своего канала; нет или неоднозначно — None.

    None означает «только ручная загрузка»: апдейтер никогда не берёт
    первый попавшийся ассет и никогда не скармливает бинарник source-каналам.
    """
    if channel not in (UpdateChannel.WINDOWS_INSTALLED, UpdateChannel.WINDOWS_PORTABLE,
                       UpdateChannel.MACOS):
        return None
    matching = [a for a in manifest.assets if a.channel == channel.value]
    if channel is UpdateChannel.MACOS:
        dmg = [a for a in matching if a.name.lower().endswith(".dmg")]
        zips = [a for a in matching if a.name.lower().endswith(".zip")]
        if len(dmg) == 1:
            return dmg[0]
        if not dmg and len(zips) == 1:
            return zips[0]
        return None
    if len(matching) != 1:
        return None
    return matching[0]


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


# --- Результат обнаружения обновления -------------------------------------

@dataclass(frozen=True)
class UpdateInfo:
    """Что нашёл UpdateChecker: полный факт вместо трёх строк."""

    kind: str                 # "release" | "git" | "archive"
    suppress_id: str          # тег релиза либо SHA коммита — ключ «Игнорировать»
    title_version: str        # человекочитаемая версия для диалога
    description: str
    manual: bool = False      # только ручная загрузка (нет пригодного ассета)
    manual_url: str = ""
    asset: ReleaseAsset = None
    commit: str = ""
    zip_url: str = ""         # SHA-pinned zipball для kind == "archive"


# --- Сетевая сессия апдейтера ---------------------------------------------

DEFAULT_TIMEOUT = (10, 30)
RELEASES_PAGE = f"https://github.com/{GITHUB_REPO}/releases/latest"
_API_BASE = f"https://api.github.com/repos/{GITHUB_REPO}"


def build_updater_session(settings_manager=None):
    """requests.Session с прокси из настроек приложения.

    trust_env=False: системный прокси не должен молча перехватывать трафик
    апдейтера. Пароль в логи не попадает — URL прокси нигде не логируется.
    """
    session = requests.Session()
    session.trust_env = False
    session.max_redirects = 5
    try:
        proxy = settings_manager.load_proxy_settings() if settings_manager else {}
    except Exception:
        proxy = {}
    if proxy.get("enabled") and proxy.get("host") and proxy.get("port"):
        scheme = {"SOCKS5": "socks5h", "SOCKS4": "socks4"}.get(
            str(proxy.get("type") or "").upper(), "http")
        auth = ""
        user = str(proxy.get("user") or "")
        password = str(proxy.get("password") or "")
        if user:
            auth = quote(user, safe="")
            if password:
                auth += ":" + quote(password, safe="")
            auth += "@"
        url = f"{scheme}://{auth}{proxy['host']}:{proxy['port']}"
        session.proxies = {"http": url, "https": url}
    return session


# --- Проверка обновлений ---------------------------------------------------

class UpdateChecker(QThread):
    """Обнаруживает обновление своего канала в рабочем потоке.

    Любой сбой — error_occurred; no_update означает ровно «вы на актуальной
    версии», никогда не «что-то пошло не так».
    """

    update_available = pyqtSignal(object)  # UpdateInfo
    no_update = pyqtSignal()
    error_occurred = pyqtSignal(str)

    def __init__(self, parent=None, *, manual=False, session_factory=build_updater_session):
        super().__init__(parent)
        self._manual = manual
        self._session_factory = session_factory

    def run(self):
        try:
            channel = detect_update_channel()
            if channel in (UpdateChannel.WINDOWS_INSTALLED,
                           UpdateChannel.WINDOWS_PORTABLE,
                           UpdateChannel.MACOS):
                self._check_release(channel, read_build_identity())
            elif channel is UpdateChannel.SOURCE_GIT:
                self._check_git()
            elif channel is UpdateChannel.SOURCE_ARCHIVE:
                self._check_archive()
            else:
                self._check_development()
        except UpdateError as e:
            self.error_occurred.emit(e.user_message)
        except Exception as e:  # noqa: BLE001 — поток не должен умирать молча
            self.error_occurred.emit(str(e))

    # -- release-каналы (Windows/macOS) --

    def _fetch_latest_release(self, session):
        response = session.get(f"{_API_BASE}/releases/latest", timeout=DEFAULT_TIMEOUT)
        if response.status_code != 200:
            raise UpdateError(f"HTTP {response.status_code} при запросе релиза")
        return response.json()

    def _check_release(self, channel, identity):
        session = self._session_factory()
        data = self._fetch_latest_release(session)
        tag = str(data.get("tag_name", ""))
        remote_version = parse_version_tag(tag)
        gh_assets = data.get("assets") or []
        html_url = str(data.get("html_url") or RELEASES_PAGE)
        body = str(data.get("body") or "Доступно новое обновление.")
        manifest_url = next(
            (a.get("browser_download_url") for a in gh_assets
             if a.get("name") == UPDATE_MANIFEST_FILENAME), None)

        if manifest_url is None:
            # Легаси-релиз без манифеста: объявить можно, исполнять нельзя.
            if identity is None or remote_version > identity.version:
                self.update_available.emit(UpdateInfo(
                    kind="release", suppress_id=tag, title_version=tag.lstrip("vV"),
                    description=body, manual=True, manual_url=html_url))
            else:
                self.no_update.emit()
            return

        response = session.get(manifest_url, timeout=DEFAULT_TIMEOUT)
        if response.status_code != 200:
            raise UpdateError(f"HTTP {response.status_code} при загрузке манифеста")
        manifest = parse_update_manifest(response.json(), tag, gh_assets)
        if identity is not None and not manifest.version > identity.version:
            self.no_update.emit()
            return
        asset = select_release_asset(manifest, channel)
        if asset is None:
            self.update_available.emit(UpdateInfo(
                kind="release", suppress_id=tag, title_version=tag.lstrip("vV"),
                description=body, manual=True, manual_url=html_url))
        else:
            self.update_available.emit(UpdateInfo(
                kind="release", suppress_id=tag, title_version=tag.lstrip("vV"),
                description=body, asset=asset, commit=manifest.commit))

    def _check_development(self):
        """DEVELOPMENT: только ручное объявление, никакой автоустановки."""
        from gemini_translator.version import __version__
        session = self._session_factory()
        data = self._fetch_latest_release(session)
        tag = str(data.get("tag_name", ""))
        if parse_version_tag(tag) > parse_version_tag(__version__):
            self.update_available.emit(UpdateInfo(
                kind="release", suppress_id=tag, title_version=tag.lstrip("vV"),
                description=str(data.get("body") or "Доступно новое обновление."),
                manual=True, manual_url=str(data.get("html_url") or RELEASES_PAGE)))
        else:
            self.no_update.emit()

    # -- git-источники --

    @staticmethod
    def _git(args, timeout=60):
        return subprocess.run(
            ["git", *args], cwd=str(project_root()), capture_output=True,
            text=True, timeout=timeout,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})

    def _check_git(self):
        upstream = self._git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])
        if upstream.returncode != 0:
            raise UpdateError(
                "Для этой копии не настроена upstream-ветка — автообновление "
                "невозможно (git branch --set-upstream-to=origin/<ветка>).")
        fetch = self._git(["fetch"])
        if fetch.returncode != 0:
            raise UpdateError(f"git fetch завершился с ошибкой: {fetch.stderr.strip()[:400]}")
        sha_res = self._git(["rev-parse", "@{u}"])
        if sha_res.returncode != 0:
            raise UpdateError(f"Не удалось определить upstream-коммит: {sha_res.stderr.strip()[:400]}")
        sha = sha_res.stdout.strip()
        count_res = self._git(["rev-list", "--count", "HEAD..@{u}"])
        if count_res.returncode != 0:
            raise UpdateError(f"git rev-list завершился с ошибкой: {count_res.stderr.strip()[:400]}")
        count = int(count_res.stdout.strip() or "0")
        if count > 0:
            self.update_available.emit(UpdateInfo(
                kind="git", suppress_id=sha, title_version=sha[:10],
                description=f"Доступны обновления на GitHub ({count} новых коммитов).",
                commit=sha))
        else:
            self.no_update.emit()

    # -- source-архивы --

    def _check_archive(self):
        identity = read_archive_identity(project_root())
        if identity is None:
            self.update_available.emit(UpdateInfo(
                kind="archive", suppress_id="unknown-archive", title_version="?",
                description=("Не удалось определить установленную ревизию исходников. "
                             "Скачайте свежий архив вручную."),
                manual=True, manual_url=RELEASES_PAGE))
            return
        session = self._session_factory()
        response = session.get(f"{_API_BASE}/commits/main", timeout=DEFAULT_TIMEOUT)
        if response.status_code != 200:
            raise UpdateError(f"HTTP {response.status_code} при запросе коммитов")
        data = response.json()
        sha = str(data.get("sha") or "")
        if not _HEX40_RE.match(sha):
            raise UpdateError("GitHub вернул некорректный ответ о последнем коммите")
        if sha == identity["commit"]:
            self.no_update.emit()
            return
        message = (data.get("commit") or {}).get("message") or "Доступно обновление исходного кода."
        self.update_available.emit(UpdateInfo(
            kind="archive", suppress_id=sha, title_version=sha[:10],
            description=f"Найден новый коммит:\n{message}", commit=sha,
            zip_url=f"{_API_BASE}/zipball/{sha}"))


# --- Загрузка с верификацией ----------------------------------------------

class UpdateDownloader(QThread):
    """Скачивает ассет во .part-файл и проверяет его до переименования.

    Завершение скачивания — ещё не успех: файл считается пригодным только
    после проверки размера, SHA-256 и формы (PE/ZIP). Любой сбой и отмена
    удаляют .part; итоговое имя появляется только у проверенного файла.
    """

    progress = pyqtSignal(int, int)   # скачано, всего (0 — неизвестно)
    verified = pyqtSignal(str)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()

    _CHUNK = 64 * 1024

    def __init__(self, url, staging_dir, *, expected_size=None, expected_sha256=None,
                 shape=None, session_factory=build_updater_session, parent=None):
        super().__init__(parent)
        self._url = url
        self._staging_dir = Path(staging_dir)
        self._expected_size = expected_size
        self._expected_sha256 = (expected_sha256 or "").lower() or None
        self._shape = shape
        self._session_factory = session_factory
        self._cancel_requested = False
        self._response = None

    def cancel(self):
        self._cancel_requested = True
        response = self._response
        if response is not None:
            try:
                response.close()
            except Exception:
                pass

    def run(self):
        import hashlib

        final_name = self._url.rstrip("/").rsplit("/", 1)[-1] or "update.bin"
        self._staging_dir.mkdir(parents=True, exist_ok=True)
        part_path = self._staging_dir / (final_name + ".part")
        final_path = self._staging_dir / final_name
        try:
            session = self._session_factory()
            response = session.get(self._url, timeout=DEFAULT_TIMEOUT, stream=True)
            self._response = response
            if response.status_code != 200:
                raise UpdateError(f"HTTP {response.status_code} при скачивании обновления")
            declared = self._expected_size
            if declared is None:
                try:
                    declared = int(response.headers.get("content-length", 0)) or None
                except (TypeError, ValueError):
                    declared = None
            digest = hashlib.sha256()
            downloaded = 0
            with open(part_path, "wb") as out:
                for chunk in response.iter_content(chunk_size=self._CHUNK):
                    if self._cancel_requested:
                        raise _Cancelled()
                    if not chunk:
                        continue
                    out.write(chunk)
                    digest.update(chunk)
                    downloaded += len(chunk)
                    self.progress.emit(downloaded, declared or 0)
            if self._cancel_requested:
                raise _Cancelled()
            if self._expected_size is not None and downloaded != self._expected_size:
                raise UpdateError(
                    f"Размер файла не совпал: получено {downloaded}, "
                    f"ожидалось {self._expected_size}")
            if self._expected_sha256 is not None and digest.hexdigest() != self._expected_sha256:
                raise UpdateError("SHA-256 скачанного файла не совпал с манифестом")
            self._verify_shape(part_path)
            os.replace(part_path, final_path)
            self.verified.emit(str(final_path))
        except _Cancelled:
            self._cleanup(part_path)
            self.cancelled.emit()
        except UpdateError as e:
            self._cleanup(part_path)
            self.failed.emit(e.user_message)
        except Exception as e:  # noqa: BLE001
            self._cleanup(part_path)
            self.failed.emit(str(e))
        finally:
            self._response = None

    def _verify_shape(self, path):
        import zipfile

        if self._shape == "pe":
            with open(path, "rb") as f:
                if f.read(2) != b"MZ":
                    raise UpdateError("Скачанный файл не является исполняемым файлом Windows")
        elif self._shape == "zip":
            try:
                with zipfile.ZipFile(path) as z:
                    if not z.namelist():
                        raise UpdateError("Скачанный архив пуст")
                    bad = z.testzip()
                    if bad is not None:
                        raise UpdateError(f"Архив повреждён: {bad}")
            except zipfile.BadZipFile as e:
                raise UpdateError(f"Архив повреждён: {e}") from e

    @staticmethod
    def _cleanup(part_path):
        try:
            os.remove(part_path)
        except OSError:
            pass


class _Cancelled(Exception):
    pass


class FunctionWorker(QThread):
    """Выполняет callable в рабочем потоке: done(result) либо failed(msg)."""

    done = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self):
        try:
            self.done.emit(self._fn())
        except Exception as e:  # noqa: BLE001
            self.failed.emit(getattr(e, "user_message", None) or str(e))
