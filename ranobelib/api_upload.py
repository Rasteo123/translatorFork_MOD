import json
import logging
import re
import time
import traceback
from datetime import datetime, timedelta
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from PyQt6.QtCore import QThread, pyqtSignal

from constants import BROWSER_ARGS, BROWSER_PROFILE_DIR
from models import ChapterData
from utils import format_num, format_timedelta

API_BASE = "https://api.cdnlibs.org/api"
SITE_BASE = "https://ranobelib.me"
API_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/146.0.0.0 Safari/537.36"
)


class RanobeLibApiError(RuntimeError):
    def __init__(self, method: str, pathname: str, status_code: int, payload):
        self.method = method
        self.pathname = pathname
        self.status_code = status_code
        self.payload = payload
        super().__init__(
            f"{method} {pathname} failed: {status_code} {_stringify_payload(payload)}"
        )


def _stringify_payload(payload) -> str:
    if isinstance(payload, (dict, list)):
        return json.dumps(payload, ensure_ascii=False)
    return str(payload)


def _safe_int(value, default=0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value, default=0.0) -> float:
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return default


def _extract_slug_from_url(raw: str) -> str:
    value = (raw or "").strip()
    if not value:
        raise ValueError("Пустой URL RanobeLib.")

    if re.fullmatch(r"\d+--[^/\s?#]+", value):
        return value

    match = re.search(r"/ru/book/([^/?#]+)", value)
    if not match:
        raise ValueError(
            "Не удалось извлечь slug книги из URL. Нужен URL вида ranobelib.me/ru/book/... ."
        )
    return match.group(1)


def _parse_manga_id(slug: str) -> int:
    match = re.match(r"^(\d+)--", slug)
    if not match:
        raise ValueError(f"Не удалось извлечь manga id из slug: {slug}")
    return int(match.group(1))


def _json_request(pathname: str, method: str = "GET", token: str | None = None, body=None):
    headers = {
        "Accept": "application/json",
        "Origin": SITE_BASE,
        "Referer": f"{SITE_BASE}/",
        "User-Agent": API_USER_AGENT,
    }
    payload = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        headers["Content-Type"] = "application/json"
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")

    request = Request(
        f"{API_BASE}{pathname}",
        data=payload,
        headers=headers,
        method=method,
    )

    try:
        with urlopen(request, timeout=60) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as error:
        raw = error.read().decode("utf-8", errors="replace")
        parsed = _parse_json_response(raw)
        if error.code == 403 and pathname in {"/auth/me", "/auth/oauth/token"}:
            raise RuntimeError(
                "RanobeLib API отклонил прямую проверку авторизации (403). "
                "Переключитесь на режим «Через браузер» и выполните загрузку через него."
            ) from error
        raise RanobeLibApiError(method, pathname, error.code, parsed) from error
    except URLError as error:
        raise RuntimeError(f"Ошибка сети RanobeLib API: {error.reason}") from error

    return _parse_json_response(raw)


def _parse_json_response(raw: str):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _extract_stored_auth(local_storage_dump: dict) -> dict | None:
    if not isinstance(local_storage_dump, dict):
        return None

    for value in local_storage_dump.values():
        if not isinstance(value, str):
            continue
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            continue

        if not isinstance(parsed, dict):
            continue

        token = parsed.get("token")
        if isinstance(token, dict) and token.get("access_token"):
            return parsed
        if parsed.get("access_token") and parsed.get("refresh_token"):
            return {"token": parsed}
    return None


def _dump_local_storage(page) -> dict:
    return page.evaluate(
        """() => {
            const result = {};
            for (let index = 0; index < localStorage.length; index += 1) {
                const key = localStorage.key(index);
                if (key) {
                    result[key] = localStorage.getItem(key);
                }
            }
            return result;
        }"""
    )


def _read_stored_auth_from_context(context) -> dict | None:
    for page in reversed(context.pages):
        try:
            if not page.url or not str(page.url).startswith(SITE_BASE):
                continue
            stored_auth = _extract_stored_auth(_dump_local_storage(page))
            if stored_auth and stored_auth.get("token", {}).get("access_token"):
                return stored_auth
        except Exception:
            continue
    return None


def _fetch_auth_me(token: str):
    response = _json_request("/auth/me", token=token)
    if isinstance(response, dict) and "data" in response:
        return response["data"]
    return response


def _refresh_token(refresh_token_value: str) -> dict:
    response = _json_request(
        "/auth/oauth/token",
        method="POST",
        body={
            "grant_type": "refresh_token",
            "client_id": "1",
            "refresh_token": refresh_token_value,
            "scope": "",
        },
    )
    if isinstance(response, dict) and "data" in response:
        return response["data"]
    return response


def resolve_api_auth(slug: str) -> tuple[dict, dict]:
    stored_auth = None
    try:
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(BROWSER_PROFILE_DIR),
                headless=True,
                viewport={"width": 1280, "height": 900},
                args=BROWSER_ARGS,
            )
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(
                    f"{SITE_BASE}/ru/book/{slug}?section=chapters",
                    wait_until="domcontentloaded",
                    timeout=30000,
                )
                page.wait_for_timeout(1500)
                stored_auth = _extract_stored_auth(_dump_local_storage(page))
                if not stored_auth:
                    stored_auth = _read_stored_auth_from_context(context)
            finally:
                context.close()
    except Exception as error:
        raise RuntimeError(
            f"Не удалось получить авторизацию RanobeLib из профиля Chrome: {error}"
        ) from error

    if not stored_auth or not stored_auth.get("token"):
        raise RuntimeError(
            "Не найдена сохранённая сессия RanobeLib. Нажмите «Войти в RanobeLib» и авторизуйтесь."
        )

    token = stored_auth["token"]
    refresh_token_value = token.get("refresh_token")
    expires_at = _safe_int(token.get("timestamp"), int(time.time() * 1000)) + (
        _safe_int(token.get("expires_in"), 0) * 1000
    )
    if refresh_token_value and (
        not token.get("access_token") or expires_at <= int(time.time() * 1000)
    ):
        token = _refresh_token(refresh_token_value)

    access_token = token.get("access_token")
    if not access_token:
        raise RuntimeError(
            "Не удалось получить access token RanobeLib. Повторите вход через кнопку авторизации."
        )

    auth = _fetch_auth_me(access_token)
    return token, auth


def fetch_existing_chapters(slug: str) -> list[dict]:
    response = _json_request(f"/manga/{slug}/chapters")
    data = response.get("data") if isinstance(response, dict) else None
    return data if isinstance(data, list) else []


def _get_latest_chapter_config(chapters: list[dict], requested_volume: str):
    same_volume = [
        chapter
        for chapter in chapters
        if str(chapter.get("volume")) == str(requested_volume)
    ]
    same_volume.sort(key=lambda chapter: _safe_float(chapter.get("number")))
    sorted_all = sorted(chapters, key=lambda chapter: _safe_float(chapter.get("number")))

    latest = same_volume[-1] if same_volume else (sorted_all[-1] if sorted_all else None)
    if not latest:
        return None, [], None

    branches = latest.get("branches") or []
    branch_source = branches[0] if branches else latest
    team_ids = []
    for team in branch_source.get("teams") or []:
        team_id = _safe_int(team.get("id"), 0)
        if team_id > 0:
            team_ids.append(team_id)

    branch_id = branch_source.get("branch_id", latest.get("branch_id"))
    if branch_id is not None:
        branch_id = _safe_int(branch_id, 0) or None
    return latest, team_ids, branch_id


def _chapter_identity(volume, number) -> str:
    return f"{volume}:{format_num(_safe_float(number))}"


def _content_blocks(content: str) -> list[str]:
    text = (content or "").strip()
    if not text:
        return []

    if text.startswith("<"):
        soup = BeautifulSoup(text, "html.parser")
        blocks = []
        for tag in soup.find_all(["p", "div", "li", "blockquote"]):
            block = re.sub(r"\s+", " ", tag.get_text(" ", strip=True)).strip()
            if block:
                blocks.append(block)
        if blocks:
            return blocks
        text = soup.get_text("\n", strip=True)

    normalized = text.replace("\r\n", "\n")

    # Mirror the browser uploader: each non-empty line becomes its own paragraph.
    return [
        re.sub(r"\s+", " ", line).strip()
        for line in normalized.split("\n")
        if line.strip()
    ]


def _content_to_doc(content: str) -> dict:
    blocks = _content_blocks(content)
    if not blocks:
        raise ValueError("Текст главы пуст после подготовки контента для API.")
    return {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": block}],
            }
            for block in blocks
        ],
    }


def _format_publish_at(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%d %H:%M:00")


def _build_payload(
    chapter: ChapterData,
    manga_id: int,
    team_ids: list[int],
    branch_id: int | None,
    publish_at: datetime | None,
) -> dict:
    return {
        "volume": str(chapter.volume),
        "number": format_num(chapter.number),
        "name": chapter.title or "",
        "branch_id": branch_id,
        "content": _content_to_doc(chapter.content),
        "manga_id": manga_id,
        "teams": team_ids,
        "pages": [],
        "publish_at": _format_publish_at(publish_at),
        "expired_type": 0,
        "bundle_id": None,
        "attachments": [],
    }


def _is_duplicate_error(error: Exception) -> bool:
    if not isinstance(error, RanobeLibApiError):
        return False
    if error.status_code != 422:
        return False
    message = _stringify_payload(error.payload).lower()
    return "существ" in message or "already exists" in message or "exists" in message


class ApiUploadWorker(QThread):
    log_signal = pyqtSignal(str, str)
    progress_signal = pyqtSignal(int)
    stats_signal = pyqtSignal(int, int, int)
    eta_signal = pyqtSignal(str)
    finished_signal = pyqtSignal()
    chapter_done_signal = pyqtSignal(int)

    def __init__(
        self,
        url: str,
        chapters_list: list[ChapterData],
        schedule_enabled: bool,
        start_time: datetime,
        interval_minutes: int,
        paid_enabled: bool,
        price: int,
        force_num: bool,
    ):
        super().__init__()
        self.url = url
        self.chapters_list = chapters_list
        self.schedule_enabled = schedule_enabled
        self.current_publish_time = start_time
        self.interval_minutes = interval_minutes
        self.paid_enabled = paid_enabled
        self.price = price
        self.force_num = force_num
        self.is_running = True

        self._ok = 0
        self._errors = 0
        self._skipped = 0
        self._times: list[float] = []
        self._config_cache: dict[str, tuple[list[int], int | None]] = {}
        self._fallback_logged_for_volume: set[str] = set()

    def log(self, level: str, msg: str):
        self.log_signal.emit(level, msg)

    def stop(self):
        self.is_running = False

    def _resolve_target_config(
        self,
        chapter: ChapterData,
        existing_chapters: list[dict],
        auth_team_ids: list[int],
    ) -> tuple[list[int], int | None]:
        volume_key = str(chapter.volume)
        cached = self._config_cache.get(volume_key)
        if cached:
            return cached

        same_volume = [
            existing for existing in existing_chapters
            if str(existing.get("volume")) == volume_key
        ]
        latest_source = same_volume or existing_chapters
        _latest, team_ids, branch_id = _get_latest_chapter_config(latest_source, volume_key)

        if team_ids:
            available_team_ids = [team_id for team_id in team_ids if team_id in auth_team_ids]
            if available_team_ids and available_team_ids != team_ids:
                self.log(
                    "WARNING",
                    f"API: не все команды последней главы доступны текущему аккаунту, "
                    f"использую {', '.join(str(team_id) for team_id in available_team_ids)}.",
                )
                team_ids = available_team_ids

        if not team_ids:
            if len(auth_team_ids) == 1:
                team_ids = [auth_team_ids[0]]
                if volume_key not in self._fallback_logged_for_volume:
                    self._fallback_logged_for_volume.add(volume_key)
                    self.log(
                        "INFO",
                        f"API: команда для Т.{volume_key} не найдена в последних главах, "
                        f"использую единственную доступную команду {team_ids[0]}.",
                    )
            else:
                raise RuntimeError(
                    "Не удалось автоматически определить команду для API-загрузки. "
                    "Если это новая книга без глав, сначала авторизуйтесь нужным аккаунтом "
                    "и залейте первую главу браузерным способом."
                )

        missing_team_ids = [team_id for team_id in team_ids if team_id not in auth_team_ids]
        if missing_team_ids:
            raise RuntimeError(
                "Текущий аккаунт не состоит в нужной команде RanobeLib: "
                + ", ".join(str(team_id) for team_id in missing_team_ids)
            )

        config = (team_ids, branch_id)
        self._config_cache[volume_key] = config
        return config

    def _remember_created_chapter(
        self,
        existing_chapters: list[dict],
        existing_keys: set[str],
        chapter: ChapterData,
        team_ids: list[int],
        branch_id: int | None,
    ):
        existing_keys.add(_chapter_identity(chapter.volume, chapter.number))
        teams = [{"id": team_id} for team_id in team_ids]
        existing_chapters.append(
            {
                "volume": str(chapter.volume),
                "number": format_num(chapter.number),
                "branch_id": branch_id,
                "teams": teams,
                "branches": [{"branch_id": branch_id, "teams": teams}],
            }
        )

    def run(self):
        total = len(self.chapters_list)
        try:
            if self.paid_enabled:
                raise RuntimeError(
                    "API-режим пока не поддерживает платные главы. Используйте браузерный режим."
                )

            slug = _extract_slug_from_url(self.url)
            manga_id = _parse_manga_id(slug)

            self.log("INFO", "API: получаю авторизацию RanobeLib из сохранённого профиля...")
            token, auth = resolve_api_auth(slug)
            auth_team_ids = [
                _safe_int(team.get("id"), 0)
                for team in (auth.get("teams") or [])
                if _safe_int(team.get("id"), 0) > 0
            ]
            self.log(
                "SUCCESS",
                f"API: авторизован как {auth.get('username') or auth.get('id') or 'unknown'}",
            )

            existing_chapters = fetch_existing_chapters(slug)
            existing_keys = {
                _chapter_identity(chapter.get("volume"), chapter.get("number"))
                for chapter in existing_chapters
            }
            self.log(
                "INFO",
                f"API: найдено глав на RanobeLib: {len(existing_chapters)}",
            )

            for index, chapter in enumerate(self.chapters_list):
                if not self.is_running:
                    self._skipped += total - index
                    break

                parts = []
                if self.schedule_enabled:
                    parts.append(self.current_publish_time.strftime("%d.%m %H:%M"))
                else:
                    parts.append("Сразу")
                self.log(
                    "INFO",
                    f"[{index + 1}/{total}] API Т.{chapter.volume} Гл.{format_num(chapter.number)} "
                    f"({', '.join(parts)})",
                )

                started_at = time.monotonic()
                try:
                    chapter_key = _chapter_identity(chapter.volume, chapter.number)
                    if chapter_key in existing_keys:
                        self._skipped += 1
                        self.chapter_done_signal.emit(index)
                        self.log(
                            "WARNING",
                            f"API: Т.{chapter.volume} Гл.{format_num(chapter.number)} уже существует, пропускаю.",
                        )
                    else:
                        team_ids, branch_id = self._resolve_target_config(
                            chapter, existing_chapters, auth_team_ids
                        )
                        publish_at = self.current_publish_time if self.schedule_enabled else None
                        payload = _build_payload(
                            chapter=chapter,
                            manga_id=manga_id,
                            team_ids=team_ids,
                            branch_id=branch_id,
                            publish_at=publish_at,
                        )
                        response = _json_request(
                            "/chapters",
                            method="POST",
                            token=token["access_token"],
                            body=payload,
                        )
                        created = response.get("data") if isinstance(response, dict) else response
                        self._ok += 1
                        self.chapter_done_signal.emit(index)
                        self._remember_created_chapter(
                            existing_chapters,
                            existing_keys,
                            chapter,
                            team_ids,
                            branch_id,
                        )
                        if self.schedule_enabled:
                            self.current_publish_time += timedelta(minutes=self.interval_minutes)
                        created_id = created.get("id") if isinstance(created, dict) else None
                        self.log(
                            "SUCCESS",
                            f"API: глава {format_num(chapter.number)} сохранена"
                            + (f" (id={created_id})" if created_id else ""),
                        )
                except Exception as error:
                    if _is_duplicate_error(error):
                        self._skipped += 1
                        existing_keys.add(_chapter_identity(chapter.volume, chapter.number))
                        self.chapter_done_signal.emit(index)
                        self.log(
                            "WARNING",
                            f"API: Т.{chapter.volume} Гл.{format_num(chapter.number)} уже существует, пропускаю.",
                        )
                    else:
                        self._errors += 1
                        self.log(
                            "ERROR",
                            f"API: ошибка для Т.{chapter.volume} Гл.{format_num(chapter.number)}: {error}",
                        )
                        logging.error(traceback.format_exc())

                self._times.append(time.monotonic() - started_at)
                progress = int(((index + 1) / total) * 100)
                self.progress_signal.emit(progress)
                self.stats_signal.emit(self._ok, self._errors, self._skipped)

                remaining = total - (index + 1)
                if remaining > 0 and self._times:
                    avg = sum(self._times) / len(self._times)
                    eta = timedelta(seconds=avg * remaining)
                    self.eta_signal.emit(f"~{format_timedelta(eta)}")
                else:
                    self.eta_signal.emit("—")

        except Exception as error:
            self.log("ERROR", f"Критическая ошибка API-загрузки: {error}")
            logging.error(traceback.format_exc())

        self.stats_signal.emit(self._ok, self._errors, self._skipped)
        self.finished_signal.emit()
