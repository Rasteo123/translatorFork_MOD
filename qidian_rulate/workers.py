# -*- coding: utf-8 -*-

from __future__ import annotations

import atexit
import asyncio
import html
import json
import os
import re
import subprocess
import sys
import time
import traceback
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote, urljoin, urlparse

import requests
from PyQt6.QtCore import QThread, pyqtSignal

from gemini_translator.api import config as api_config
from gemini_translator.api.factory import get_api_handler_class

from .models import PreparedRulateMetadata, QidianBookMetadata, RulateBookDraft

try:
    from ranobelib.constants import BROWSER_ARGS
except Exception:
    BROWSER_ARGS = [
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
        "--disable-infobars",
    ]

QIDIAN_RULATE_APP_DATA_DIR = Path.home() / ".qidian_rulate_creator"
RULATE_PROFILE_DIR = Path(
    os.environ.get(
        "QIDIAN_RULATE_PROFILE_DIR",
        str(QIDIAN_RULATE_APP_DATA_DIR / "rulate_profile"),
    )
)
RULATE_PROFILE_DIR.mkdir(parents=True, exist_ok=True)


QIDIAN_BOOK_RE = re.compile(r"^https?://(?:www\.)?qidian\.com/book/\d+/?(?:[?#].*)?$", re.IGNORECASE)
FANQIE_BOOK_RE = re.compile(r"^https?://(?:www\.)?fanqienovel\.com/page/\d+/?(?:[?#].*)?$", re.IGNORECASE)
RULATE_CATEGORY_URL = "https://tl.rulate.ru/book/0/edit/cat"
RULATE_INFO_URL = "https://tl.rulate.ru/book/0/edit/info#general"
RULATE_LOGIN_URL = "https://tl.rulate.ru/book/0/edit/info"
RULATE_BOOK_TYPE_TITLE = "Книга"
RULATE_BOOK_TYPE_DESCRIPTION = "Публикуйте свои произведения"
RULATE_BOOK_TYPE_SELECTOR = 'a.create-card.card-book[href*="typ=A"]'
RULATE_CHINESE_CATEGORY_TITLE = "Китайские"
QIDIAN_COVER_PROMPT_CHAPTER_COUNT = 3
QIDIAN_COVER_PROMPT_MAX_CHARS = 18000
TOMATO_WEB_URL_ENV = "TOMATO_NOVEL_WEB_URL"
TOMATO_WEB_PASSWORD_ENV = "TOMATO_NOVEL_WEB_PASSWORD"
TOMATO_SAVE_DIR_ENV = "TOMATO_NOVEL_SAVE_DIR"
TOMATO_EXE_ENV = "TOMATO_NOVEL_DOWNLOADER_EXE"
TOMATO_AUTO_START_ENV = "TOMATO_NOVEL_AUTO_START"
TOMATO_WEB_DEFAULT_URL = "http://127.0.0.1:18423"
TOMATO_JOB_TIMEOUT_SECONDS = 180
TOMATO_STARTUP_TIMEOUT_SECONDS = 30
TOMATO_EXE_PATTERNS = (
    "Tomato-Novel-Downloader*.exe",
    "TomatoNovelDownloader*.exe",
    "tomato-novel-downloader*.exe",
    "tomato*downloader*.exe",
)
_TOMATO_AUTOSTART_PROCESS: subprocess.Popen | None = None
_TOMATO_AUTOSTART_CLEANUP_REGISTERED = False

QIDIAN_DESCRIPTION_HEADER = "作品简介"
QIDIAN_DESCRIPTION_HEADERS = {
    "作品简介",
    "内容简介",
    "书籍简介",
    "小说简介",
    "作品介绍",
    "内容介绍",
}
QIDIAN_DESCRIPTION_STOP_LINES = {
    "男生月票榜",
    "女生月票榜",
    "月票",
    "推荐票",
    "打赏",
    "本月票数",
    "本周打赏人数",
    "包含本书的书单",
    "目录",
    "书友互动",
    "本书荣誉",
}

RULATE_GENRES = [
    "боевик",
    "боевые искусства",
    "городское фэнтези",
    "детектив",
    "драма",
    "киберпанк",
    "комедия",
    "литрпг",
    "мистика",
    "научная фантастика",
    "повседневность",
    "постапокалиптика",
    "приключения",
    "психология",
    "романтика",
    "сверхъестественное",
    "сэйнэн",
    "сюаньхуань",
    "сянься (XianXia)",
    "триллер",
    "ужасы",
    "уся (wuxia)",
    "фантастика",
    "фэнтези",
]

FALLBACK_GENRES = ["фэнтези", "мистика", "приключения"]
TAGS_FILE_ENV = "RULATE_TAGS_FILE"
_RULATE_TAGS_CACHE: list[str] | None = None


def validate_qidian_url(url: str) -> bool:
    return bool(QIDIAN_BOOK_RE.match((url or "").strip()))


def validate_fanqie_url(url: str) -> bool:
    return bool(FANQIE_BOOK_RE.match((url or "").strip()))


def validate_source_url(url: str) -> bool:
    url = (url or "").strip()
    return validate_qidian_url(url) or validate_fanqie_url(url)


def _source_name(url: str) -> str:
    if validate_fanqie_url(url):
        return "Fanqie"
    return "Qidian"


def _tag_file_candidates() -> list[Path]:
    candidates: list[Path] = []
    env_value = os.environ.get(TAGS_FILE_ENV)
    if env_value:
        candidates.append(Path(env_value))

    module_root = Path(__file__).resolve().parents[1]
    candidates.extend([
        module_root / "qidian_rulate" / "tags.txt",
        module_root / "tags.txt",
        Path.cwd() / "tags.txt",
    ])

    unique = []
    seen = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except Exception:
            resolved = candidate
        key = str(resolved).lower()
        if key not in seen:
            seen.add(key)
            unique.append(resolved)
    return unique


def load_rulate_tags() -> list[str]:
    global _RULATE_TAGS_CACHE
    if _RULATE_TAGS_CACHE is not None:
        return list(_RULATE_TAGS_CACHE)

    for path in _tag_file_candidates():
        if not path.exists() or not path.is_file():
            continue
        for encoding in ("utf-8-sig", "utf-8", "cp1251"):
            try:
                raw_lines = path.read_text(encoding=encoding).splitlines()
                break
            except UnicodeDecodeError:
                continue
        else:
            continue

        tags = []
        seen = set()
        for line in raw_lines:
            tag = _clean_text(line)
            key = tag.lower()
            if tag and key not in seen:
                seen.add(key)
                tags.append(tag)
        _RULATE_TAGS_CACHE = tags
        return list(tags)

    _RULATE_TAGS_CACHE = []
    return []


def _fallback_tags_from_allowed(allowed_tags: list[str]) -> list[str]:
    result = []
    for tag in allowed_tags:
        if tag not in result:
            result.append(tag)
        if len(result) >= 3:
            return result
    return result


def normalize_rulate_tags(value) -> list[str]:
    allowed_tags = load_rulate_tags()
    if not allowed_tags:
        searched = ", ".join(str(path) for path in _tag_file_candidates())
        raise ValueError(f"Файл tags.txt с тегами Rulate не найден. Проверенные пути: {searched}")
    return _normalize_list(
        value,
        allowed=allowed_tags,
        fallback=_fallback_tags_from_allowed(allowed_tags),
    )


def configure_playwright_runtime() -> None:
    if sys.platform == "win32" and hasattr(asyncio, "WindowsProactorEventLoopPolicy"):
        try:
            current_policy = asyncio.get_event_loop_policy()
        except Exception:
            current_policy = None
        if not isinstance(current_policy, asyncio.WindowsProactorEventLoopPolicy):
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    resolved_paths = {
        "PLAYWRIGHT_BROWSERS_PATH": api_config.find_playwright_browsers_path(),
        "PLAYWRIGHT_NODEJS_PATH": api_config.find_node_executable(),
        "PLAYWRIGHT_PACKAGE_ROOT": api_config.find_playwright_package_root(),
    }
    for env_name, resolved_path in resolved_paths.items():
        if not resolved_path:
            continue
        path_obj = Path(resolved_path)
        if path_obj.exists():
            os.environ[env_name] = str(path_obj)


def _playwright_browser_install_hint() -> str:
    python_executable = sys.executable or "python"
    return (
        "Playwright не нашел совместимый Chromium. "
        f"Установите браузер командой: \"{python_executable}\" -m playwright install chromium"
    )


def _is_browser_missing_error(error: Exception) -> bool:
    text = str(error).lower()
    return (
        "executable doesn't exist" in text
        or "playwright install" in text
        or "browserType.launch" in text and "executable" in text
        or "chromium distribution" in text and "not found" in text
    )


def _candidate_browser_cache_roots() -> list[Path]:
    roots: list[Path] = []
    for env_name in ("PLAYWRIGHT_BROWSERS_PATH",):
        env_value = os.environ.get(env_name)
        if env_value:
            roots.append(Path(env_value))

    try:
        executable_dir = api_config.get_executable_dir()
    except Exception:
        executable_dir = None
    try:
        dev_root = api_config.get_dev_project_root()
    except Exception:
        dev_root = None

    module_root = Path(__file__).resolve().parents[1]
    for base in (module_root, executable_dir, dev_root, Path.cwd()):
        if base:
            roots.append(Path(base) / "playwright_runtime" / "ms-playwright")

    localappdata = os.environ.get("LOCALAPPDATA")
    if localappdata:
        roots.append(Path(localappdata) / "ms-playwright")

    unique = []
    seen = set()
    for root in roots:
        try:
            resolved = root.resolve()
        except Exception:
            resolved = root
        key = str(resolved).lower()
        if key not in seen and resolved.exists() and resolved.is_dir():
            seen.add(key)
            unique.append(resolved)
    return unique


def _revision_from_path(path: Path) -> int:
    match = re.search(r"chromium-(\d+)", str(path))
    if not match:
        return -1
    return int(match.group(1))


def _find_cached_chromium_executable() -> Path | None:
    candidates: list[Path] = []
    for root in _candidate_browser_cache_roots():
        candidates.extend(root.glob("chromium-*/chrome-win*/chrome.exe"))
    existing = [candidate for candidate in candidates if candidate.exists() and candidate.is_file()]
    if not existing:
        return None
    return max(existing, key=_revision_from_path)


def _launch_chromium(playwright, *, headless: bool, log_callback=None):
    try:
        return playwright.chromium.launch(
            headless=headless,
            args=BROWSER_ARGS,
        )
    except Exception as error:
        if not _is_browser_missing_error(error):
            raise
        if log_callback:
            log_callback("WARNING", "Playwright Chromium не найден, пробую fallback-браузер.")

    cached_executable = _find_cached_chromium_executable()
    if cached_executable:
        try:
            if log_callback:
                log_callback("INFO", f"Playwright: запускаю Chromium из {cached_executable}.")
            return playwright.chromium.launch(
                executable_path=str(cached_executable),
                headless=headless,
                args=BROWSER_ARGS,
            )
        except Exception as error:
            if log_callback:
                log_callback("WARNING", f"Кэшированный Chromium не запустился: {error}")

    for channel in ("chrome", "msedge"):
        try:
            if log_callback:
                log_callback("INFO", f"Playwright: пробую системный браузер {channel}.")
            return playwright.chromium.launch(
                channel=channel,
                headless=headless,
                args=BROWSER_ARGS,
            )
        except Exception as error:
            if log_callback:
                log_callback("WARNING", f"Системный браузер {channel} не запустился: {error}")

    raise RuntimeError(_playwright_browser_install_hint())


def _launch_persistent_chromium_context(playwright, *, user_data_dir: str, viewport: dict, log_callback=None):
    try:
        return playwright.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=False,
            viewport=viewport,
            args=BROWSER_ARGS,
        )
    except Exception as error:
        if not _is_browser_missing_error(error):
            raise
        if log_callback:
            log_callback("WARNING", "Playwright Chromium не найден, пробую fallback-браузер.")

    cached_executable = _find_cached_chromium_executable()
    if cached_executable:
        try:
            if log_callback:
                log_callback("INFO", f"Playwright: запускаю Chromium из {cached_executable}.")
            return playwright.chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                executable_path=str(cached_executable),
                headless=False,
                viewport=viewport,
                args=BROWSER_ARGS,
            )
        except Exception as error:
            if log_callback:
                log_callback("WARNING", f"Кэшированный Chromium не запустился: {error}")

    for channel in ("chrome", "msedge"):
        try:
            if log_callback:
                log_callback("INFO", f"Playwright: пробую системный браузер {channel}.")
            return playwright.chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                channel=channel,
                headless=False,
                viewport=viewport,
                args=BROWSER_ARGS,
            )
        except Exception as error:
            if log_callback:
                log_callback("WARNING", f"Системный браузер {channel} не запустился: {error}")

    raise RuntimeError(_playwright_browser_install_hint())


def _clean_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def _clean_qidian_description(value: str | None, *, title: str = "", author: str = "") -> str:
    description = _clean_multiline(value)
    if not description:
        return ""

    title = _clean_text(title)
    author = _clean_text(author)
    escaped_title = re.escape(title) if title else r"[^》]+"

    seo_prefix_patterns = [
        rf"^.{0,80}?创作的[^。]{{0,120}}?《{escaped_title}》[^。]{{0,160}}?最新章节[:：][^。]*。",
        rf"^.{0,80}?创作的[^。]{{0,120}}?《{escaped_title}》，已更新[^。]*。",
    ]
    if author:
        escaped_author = re.escape(author)
        seo_prefix_patterns.insert(
            0,
            rf"^{escaped_author}创作的[^。]{{0,120}}?《{escaped_title}》[^。]{{0,160}}?最新章节[:：][^。]*。",
        )

    for pattern in seo_prefix_patterns:
        description = re.sub(pattern, "", description, count=1).strip()

    seo_suffix_patterns = [
        r"\s*(?:男生|女生)?月票榜No\.\d+.*$",
        r"\s*本书的主要角色有.*$",
        r"\s*本书主要角色有.*$",
        r"\s*本书又名.*$",
        r"\s*本书关键词.*$",
        r"\s*本书标签.*$",
    ]
    for pattern in seo_suffix_patterns:
        description = re.sub(pattern, "", description).strip()

    return _clean_multiline(description)


def _is_qidian_description_header_line(line: str) -> bool:
    line = _clean_text(line)
    if line in QIDIAN_DESCRIPTION_HEADERS:
        return True
    return any(line.endswith(header) for header in QIDIAN_DESCRIPTION_HEADERS if len(line) <= 16)


def _is_qidian_description_stop_line(line: str) -> bool:
    line = _clean_text(line)
    if line in QIDIAN_DESCRIPTION_STOP_LINES:
        return True
    return any(
        line.startswith(prefix)
        for prefix in (
            "男生月票榜",
            "女生月票榜",
            "包含本书的书单",
            "目录 ",
            "目录\t",
            "目录 连载",
        )
    )


def _is_likely_qidian_book_tag_line(line: str) -> bool:
    line = _clean_text(line)
    if not line or len(line) > 8:
        return False
    if re.search(r"[。！？!?…，、；;：:《》“”\"'（）()]", line):
        return False
    return bool(re.search(r"[\u4e00-\u9fff]", line))


def _extract_qidian_description_from_body(body_text: str | None) -> str:
    body_text = str(body_text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not any(header in body_text for header in QIDIAN_DESCRIPTION_HEADERS):
        return ""

    lines = [re.sub(r"[ \t\u00a0]+", " ", line).strip() for line in body_text.split("\n")]
    try:
        start_index = next(index for index, line in enumerate(lines) if _is_qidian_description_header_line(line))
    except StopIteration:
        return ""

    entries: list[str | None] = []
    stop_reached = False
    for line in lines[start_index + 1:]:
        if not line:
            if entries and entries[-1] is not None:
                entries.append(None)
            continue
        if _is_qidian_description_stop_line(line):
            stop_reached = True
            break
        entries.append(line)

    while entries and entries[0] is None:
        entries.pop(0)
    while entries and entries[-1] is None:
        entries.pop()

    if stop_reached and len(entries) >= 2 and entries[-2] is None and isinstance(entries[-1], str):
        if _is_likely_qidian_book_tag_line(entries[-1]):
            entries = entries[:-2]

    result_lines: list[str] = []
    for entry in entries:
        if entry is None:
            if result_lines and result_lines[-1] != "":
                result_lines.append("")
        else:
            result_lines.append(entry)

    while result_lines and not result_lines[0]:
        result_lines.pop(0)
    while result_lines and not result_lines[-1]:
        result_lines.pop()

    return _clean_multiline("\n".join(result_lines))


def _is_truncated_qidian_description(value: str) -> bool:
    value = _clean_text(value)
    if not value:
        return False
    return (
        value.endswith("…")
        or bool(re.search(r"最新章节[:：]", value))
        or bool(re.search(r"已更新\d+章", value))
    )


def _select_qidian_description(payload: dict, *, title: str = "", author: str = "") -> str:
    candidates = [
        _extract_qidian_description_from_body(payload.get("body_text")),
        payload.get("description"),
        payload.get("meta_description"),
    ]
    partial_candidates = []
    for candidate in candidates:
        description = _clean_qidian_description(candidate, title=title, author=author)
        if not description:
            continue
        if _is_truncated_qidian_description(description):
            partial_candidates.append(description)
            continue
        return description
    if partial_candidates:
        return max(partial_candidates, key=len)
    return ""


def _normalize_url(value: str | None, base_url: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if value.startswith("//"):
        return "https:" + value
    return urljoin(base_url, value)


def _download_cover_image(cover_url: str, *, referer: str) -> bytes:
    cover_url = (cover_url or "").strip()
    if not cover_url:
        return b""
    try:
        response = requests.get(
            cover_url,
            timeout=20,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
                "Referer": referer,
            },
        )
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        image_signatures = (b"\xff\xd8", b"\x89PNG", b"GIF", b"RIFF")
        if not response.content:
            return b""
        if "image" not in content_type.lower() and not response.content.startswith(image_signatures):
            return b""
        return response.content
    except Exception:
        return b""


def google_translate_title_to_english(title: str, timeout: int = 20) -> str:
    title = (title or "").strip()
    if not title:
        return ""
    endpoint = (
        "https://translate.googleapis.com/translate_a/single"
        f"?client=gtx&sl=zh-CN&tl=en&dt=t&q={quote(title)}"
    )
    response = requests.get(endpoint, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    parts = data[0] if data and isinstance(data[0], list) else []
    translated = "".join(str(part[0]) for part in parts if part and part[0])
    return _clean_text(translated)


_JSON_FIELD_RE = re.compile(r'(?:^|,)\s*"(?P<key>[A-Za-z_][A-Za-z0-9_]*)"\s*:', re.DOTALL)
_LOOSE_JSON_PARSE_FAILED = object()


def _extract_json_payload_text(raw_response: str) -> str:
    raw_response = (raw_response or "").strip()
    raw_response = re.sub(r"^```(?:json)?\s*", "", raw_response, flags=re.IGNORECASE)
    raw_response = re.sub(r"\s*```$", "", raw_response)
    match = re.search(r"\{.*\}", raw_response, re.DOTALL)
    return match.group(0) if match else raw_response


def _decode_loose_json_string(value: str) -> str:
    result = []
    index = 0
    while index < len(value):
        char = value[index]
        if char != "\\" or index + 1 >= len(value):
            result.append(char)
            index += 1
            continue

        escaped = value[index + 1]
        replacements = {
            '"': '"',
            "\\": "\\",
            "/": "/",
            "b": "\b",
            "f": "\f",
            "n": "\n",
            "r": "\r",
            "t": "\t",
        }
        if escaped in replacements:
            result.append(replacements[escaped])
            index += 2
            continue
        if escaped == "u" and index + 5 < len(value):
            hex_value = value[index + 2 : index + 6]
            if re.fullmatch(r"[0-9a-fA-F]{4}", hex_value):
                result.append(chr(int(hex_value, 16)))
                index += 6
                continue

        result.append(escaped)
        index += 2
    return "".join(result)


def _split_loose_json_items(value: str) -> list[str]:
    items = []
    start = 0
    depth = 0
    in_string = False
    escaped = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if char == "\\" and in_string:
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char in "[{":
            depth += 1
        elif char in "]}":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            items.append(value[start:index])
            start = index + 1
    items.append(value[start:])
    return items


def _parse_loose_json_value(raw_value: str):
    value = raw_value.strip().rstrip(",").strip()
    if not value:
        return ""

    try:
        return json.loads(value)
    except json.JSONDecodeError:
        pass

    if value.startswith('"'):
        last_quote = value.rfind('"')
        if last_quote > 0:
            value = value[1:last_quote]
        else:
            value = value[1:]
        return _decode_loose_json_string(value)

    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        parsed_items = []
        for item in _split_loose_json_items(inner):
            parsed = _parse_loose_json_value(item)
            if parsed is _LOOSE_JSON_PARSE_FAILED:
                return _LOOSE_JSON_PARSE_FAILED
            parsed_items.append(parsed)
        return parsed_items

    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered == "null":
        return None

    return _LOOSE_JSON_PARSE_FAILED


def _parse_loose_top_level_json_object(payload_text: str) -> dict | None:
    body = payload_text.strip()
    if body.startswith("{"):
        body = body[1:]
    if body.endswith("}"):
        body = body[:-1]

    matches = list(_JSON_FIELD_RE.finditer(body))
    if not matches:
        return None

    payload = {}
    for index, match in enumerate(matches):
        key = match.group("key")
        value_start = match.end()
        value_end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        parsed = _parse_loose_json_value(body[value_start:value_end])
        if parsed is not _LOOSE_JSON_PARSE_FAILED:
            payload[key] = parsed

    return payload or None


def _parse_json_response(raw_response: str) -> dict:
    payload_text = _extract_json_payload_text(raw_response)
    try:
        return json.loads(payload_text)
    except json.JSONDecodeError:
        repaired = _parse_loose_top_level_json_object(payload_text)
        if repaired is not None:
            return repaired
        raise


def parse_translation_metadata(raw_response: str) -> PreparedRulateMetadata:
    payload = _parse_json_response(raw_response)

    return PreparedRulateMetadata(
        english_title=_clean_text(payload.get("english_title")),
        translated_title=_clean_text(payload.get("translated_title")),
        translated_description=_clean_multiline(payload.get("translated_description")),
    )


def parse_catalog_metadata(raw_response: str) -> PreparedRulateMetadata:
    payload = _parse_json_response(raw_response)

    genres = _normalize_list(payload.get("genres"), allowed=RULATE_GENRES, fallback=FALLBACK_GENRES)
    tags = normalize_rulate_tags(payload.get("tags"))

    return PreparedRulateMetadata(
        genres=genres,
        tags=tags,
        cover_prompt=clean_cover_prompt_response(payload.get("cover_prompt")),
    )


def parse_prepared_metadata(raw_response: str) -> PreparedRulateMetadata:
    payload = _parse_json_response(raw_response)
    catalog = parse_catalog_metadata(json.dumps(payload, ensure_ascii=False))
    return PreparedRulateMetadata(
        english_title=_clean_text(payload.get("english_title")),
        translated_title=_clean_text(payload.get("translated_title")),
        translated_description=_clean_multiline(payload.get("translated_description")),
        genres=catalog.genres,
        tags=catalog.tags,
        cover_prompt=catalog.cover_prompt,
    )


def _normalize_list(value, *, allowed: list[str] | None, fallback: list[str]) -> list[str]:
    if isinstance(value, str):
        candidates = [part.strip() for part in re.split(r"[,;\n]", value) if part.strip()]
    elif isinstance(value, list):
        candidates = [_clean_text(str(part)) for part in value if _clean_text(str(part))]
    else:
        candidates = []

    if allowed:
        by_lower = {item.lower(): item for item in allowed}
        normalized = []
        for item in candidates:
            canonical = by_lower.get(item.lower())
            if canonical and canonical not in normalized:
                normalized.append(canonical)
    else:
        normalized = []
        for item in candidates:
            item = item.lower()
            if item and item not in normalized:
                normalized.append(item)

    for item in fallback:
        if len(normalized) >= 3:
            break
        if item not in normalized:
            normalized.append(item)
    return normalized[:8]


def _clean_multiline(value: str | None) -> str:
    value = str(value or "").strip()
    value = re.sub(r"\r\n?", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def build_ai_prompt(metadata: QidianBookMetadata, english_title: str) -> str:
    return f"""Ты переводишь базовые данные китайской веб-новеллы для карточки Rulate.

Верни только JSON без markdown.

Поля JSON:
- english_title: английское название. Используй это значение, если оно пригодно: {english_title!r}
- translated_title: литературное название на русском.
- translated_description: литературный русский перевод только исходного описания. Не добавляй сведения из глав, метаданные сайта, количество глав, автора, статус обновлений и не вставляй название отдельной строкой.

Исходные данные:
Китайское название: {metadata.title_original}
Автор: {metadata.author_name}
Описание:
{metadata.description}
"""


def build_catalog_prompt(
    metadata: QidianBookMetadata,
    prepared: PreparedRulateMetadata,
    chapters_text: str = "",
) -> str:
    allowed_genres = ", ".join(RULATE_GENRES)
    chapters_text = _truncate_cover_source_text(chapters_text)
    return f"""Ты подбираешь жанры, теги Rulate и промпт для генерации обложки китайской веб-новеллы.

Верни только JSON без markdown.

Поля JSON:
- genres: от 3 до 5 жанров строго из списка допустимых жанров.
- tags: от 3 до 8 существующих тегов Rulate по смыслу описания. Не придумывай новые теги и не используй заготовленный список.
- cover_prompt: единый промпт на английском для генерации обложки в DALL-E 3 / Ideogram. Используй русское название "{prepared.translated_title}" внутри блока Typography в кавычках. Формат значения строго такой: [Subject & Action], [Background & Atmosphere], [Typography: The text "{prepared.translated_title}" written in [Font Style Description], placed at the [bottom/top], professional book cover typography, legible, high contrast], [Visual Style: Manhwa style, Riot Games Splash Art, 8k, masterpiece], --ar 2:3

Допустимые жанры Rulate:
{allowed_genres}

Исходные данные:
Китайское название: {metadata.title_original}
Автор: {metadata.author_name}
Оригинальное описание источника:
{metadata.description}

Название EN:
{prepared.english_title}

Название RU:
{prepared.translated_title}

Описание RU:
{prepared.translated_description}

Текст первых глав для понимания визуала и жанра:
{chapters_text or "[текст глав не найден]"}
"""


def _clean_qidian_chapter_text(value: str | None) -> str:
    value = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = []
    for raw_line in value.split("\n"):
        line = re.sub(r"[\u3000 \t]+", " ", raw_line).strip()
        if not line:
            continue
        if re.fullmatch(r"\d+", line):
            continue
        if line in {"本章完", "未完待续"}:
            continue
        lines.append(line)
    return _clean_multiline("\n".join(lines))


def _strip_html_to_text(value: str | None) -> str:
    value = str(value or "")
    value = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)
    value = re.sub(r"(?i)</\s*p\s*>", "\n", value)
    value = re.sub(r"(?i)</\s*div\s*>", "\n", value)
    value = re.sub(r"<[^>]+>", "", value)
    return html.unescape(value)


def _is_likely_fanqie_obfuscated_text(value: str | None) -> bool:
    text = str(value or "")
    if not text:
        return False
    private_chars = sum(1 for char in text if "\ue000" <= char <= "\uf8ff")
    cjk_chars = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
    return private_chars >= 20 and private_chars > cjk_chars


def _clean_fanqie_chapter_text(value: str | None) -> str:
    text = _strip_html_to_text(value)
    if _is_likely_fanqie_obfuscated_text(text):
        return ""
    return _clean_qidian_chapter_text(text)


def _fanqie_book_id(url: str) -> str:
    match = re.search(r"/page/(\d+)", str(url or ""))
    return match.group(1) if match else ""


def _tomato_web_base_url() -> str:
    value = os.environ.get(TOMATO_WEB_URL_ENV, TOMATO_WEB_DEFAULT_URL).strip()
    if value.lower() in {"0", "false", "off", "disabled", "none"}:
        return ""
    return value.rstrip("/")


def _tomato_web_headers() -> dict[str, str]:
    password = os.environ.get(TOMATO_WEB_PASSWORD_ENV, "").strip()
    return {"x-tomato-password": password} if password else {}


def _tomato_auto_start_enabled() -> bool:
    value = os.environ.get(TOMATO_AUTO_START_ENV, "1").strip().lower()
    return value not in {"0", "false", "off", "no", "disabled", "none"}


def _tomato_web_is_local(base_url: str) -> bool:
    try:
        parsed = urlparse(base_url)
    except Exception:
        return False
    host = (parsed.hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1"}


def _tomato_bind_addr_from_base_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 18423
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"{host}:{port}"


def _tomato_executable_candidates_from_dir(directory: Path) -> list[Path]:
    if not directory.exists() or not directory.is_dir():
        return []
    candidates: list[Path] = []
    for pattern in TOMATO_EXE_PATTERNS:
        candidates.extend(directory.glob(pattern))
        candidates.extend(directory.glob(f"*/{pattern}"))
    return [candidate for candidate in candidates if candidate.exists() and candidate.is_file()]


def _find_tomato_executable() -> Path | None:
    env_value = os.environ.get(TOMATO_EXE_ENV, "").strip()
    if env_value:
        env_path = Path(env_value)
        if env_path.exists() and env_path.is_file():
            return env_path
        for candidate in _tomato_executable_candidates_from_dir(env_path):
            return candidate

    roots: list[Path] = []
    module_root = Path(__file__).resolve().parents[1]
    roots.extend([
        api_config.get_resource_path("tools/tomato"),
        api_config.get_resource_path("tomato"),
        module_root,
        module_root / "tomato",
        module_root / "tools",
        module_root / "tools" / "tomato",
        Path.cwd(),
        Path.cwd() / "tomato",
    ])
    try:
        executable_dir = api_config.get_executable_dir()
        if executable_dir:
            roots.extend([Path(executable_dir), Path(executable_dir) / "tomato", Path(executable_dir) / "tools" / "tomato"])
    except Exception:
        pass
    try:
        internal_dir = api_config.get_internal_resource_dir()
        if internal_dir:
            roots.extend([Path(internal_dir), Path(internal_dir) / "tomato", Path(internal_dir) / "tools" / "tomato"])
    except Exception:
        pass
    try:
        dev_root = api_config.get_dev_project_root()
        if dev_root:
            roots.extend([Path(dev_root), Path(dev_root) / "tomato", Path(dev_root) / "tools" / "tomato"])
    except Exception:
        pass

    downloads_dir = Path.home() / "Downloads"
    roots.extend([
        downloads_dir,
        downloads_dir / "tomato",
        downloads_dir / "Tomato-Novel-Downloader",
    ])

    seen: set[str] = set()
    for root in roots:
        try:
            resolved = root.resolve()
        except Exception:
            resolved = root
        key = str(resolved).lower()
        if key in seen:
            continue
        seen.add(key)
        candidates = _tomato_executable_candidates_from_dir(resolved)
        if candidates:
            return max(candidates, key=lambda path: path.stat().st_mtime)
    return None


def _stop_tomato_autostart_process() -> None:
    process = _TOMATO_AUTOSTART_PROCESS
    if process and process.poll() is None:
        try:
            process.terminate()
        except Exception:
            pass


def _start_tomato_web_server(
    session: requests.Session,
    base_url: str,
    headers: dict[str, str],
    *,
    log_callback=None,
) -> bool:
    global _TOMATO_AUTOSTART_PROCESS, _TOMATO_AUTOSTART_CLEANUP_REGISTERED

    def log(level: str, message: str) -> None:
        if callable(log_callback):
            log_callback(level, message)

    if not _tomato_auto_start_enabled():
        log("INFO", f"Tomato: автозапуск отключён через {TOMATO_AUTO_START_ENV}.")
        return False
    if not _tomato_web_is_local(base_url):
        log("WARNING", "Tomato: автозапуск доступен только для локального Web UI.")
        return False

    process = _TOMATO_AUTOSTART_PROCESS
    if process and process.poll() is None:
        log("INFO", "Tomato: Web UI уже запускается, жду готовности...")
    else:
        executable = _find_tomato_executable()
        if not executable:
            log(
                "WARNING",
                f"Tomato: exe не найден. Укажите путь в {TOMATO_EXE_ENV} или положите TomatoNovelDownloader*.exe рядом с программой.",
            )
            return False

        env = os.environ.copy()
        env.setdefault("TOMATO_WEB_ADDR", _tomato_bind_addr_from_base_url(base_url))
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        try:
            log("INFO", f"Tomato: запускаю Web UI из {executable}...")
            _TOMATO_AUTOSTART_PROCESS = subprocess.Popen(
                [str(executable), "--server"],
                cwd=str(executable.parent),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
        except Exception as error:
            log("WARNING", f"Tomato: не удалось запустить Web UI: {error}")
            return False

        if not _TOMATO_AUTOSTART_CLEANUP_REGISTERED:
            atexit.register(_stop_tomato_autostart_process)
            _TOMATO_AUTOSTART_CLEANUP_REGISTERED = True

    deadline = time.monotonic() + TOMATO_STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        process = _TOMATO_AUTOSTART_PROCESS
        if process and process.poll() is not None:
            log("WARNING", f"Tomato: Web UI завершился сразу после запуска с кодом {process.returncode}.")
            return False
        try:
            response = session.get(f"{base_url}/api/status", headers=headers, timeout=2.5)
            if response.ok or response.status_code == 401:
                log("SUCCESS", "Tomato: Web UI запущен.")
                return True
        except requests.RequestException:
            pass
        time.sleep(0.5)

    log("WARNING", "Tomato: Web UI не ответил после автозапуска, использую Playwright.")
    return False


def _tomato_status_folder(save_root: Path, book_id: str) -> Path:
    safe_book_id = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", book_id).strip(" ._")
    return save_root / (safe_book_id or book_id)


def _tomato_record_text(record) -> tuple[str, str]:
    if isinstance(record, list):
        title = _clean_text(str(record[0] if len(record) > 0 else ""))
        content = record[1] if len(record) > 1 else ""
        return title, str(content or "")
    if isinstance(record, dict):
        title = _clean_text(str(record.get("title") or record.get("name") or ""))
        content = record.get("content") or record.get("text") or ""
        return title, str(content or "")
    return "", ""


def _read_tomato_chapters_from_folder(folder: Path, *, limit: int = QIDIAN_COVER_PROMPT_CHAPTER_COUNT) -> str:
    records: list[tuple[str, str]] = []
    seen_ids: set[str] = set()

    journal_path = folder / "downloaded_chapters.jsonl"
    if journal_path.exists():
        try:
            with journal_path.open("r", encoding="utf-8") as handle:
                for raw_line in handle:
                    if len(records) >= limit:
                        break
                    raw_line = raw_line.strip()
                    if not raw_line:
                        continue
                    try:
                        payload = json.loads(raw_line)
                    except Exception:
                        continue
                    chapter_id = _clean_text(str(payload.get("id") or ""))
                    if chapter_id and chapter_id in seen_ids:
                        continue
                    title = _clean_text(str(payload.get("title") or ""))
                    text = _clean_fanqie_chapter_text(str(payload.get("content") or ""))
                    if not text or text == "[本章下载失败]":
                        continue
                    if chapter_id:
                        seen_ids.add(chapter_id)
                    records.append((title, text))
        except OSError:
            pass

    status_path = folder / "status.json"
    if status_path.exists() and len(records) < limit:
        try:
            payload = json.loads(status_path.read_text(encoding="utf-8"))
            downloaded = payload.get("downloaded") if isinstance(payload, dict) else None
            if isinstance(downloaded, dict):
                for chapter_id, record in downloaded.items():
                    if len(records) >= limit:
                        break
                    chapter_id = _clean_text(str(chapter_id))
                    if chapter_id and chapter_id in seen_ids:
                        continue
                    title, raw_text = _tomato_record_text(record)
                    text = _clean_fanqie_chapter_text(raw_text)
                    if not text or text == "[本章下载失败]":
                        continue
                    if chapter_id:
                        seen_ids.add(chapter_id)
                    records.append((title, text))
        except Exception:
            pass

    chapters = []
    for index, (title, text) in enumerate(records[:limit], start=1):
        chapter_title = title or f"Глава {index}"
        chapters.append(f"{chapter_title}\n{text}")
    return _truncate_cover_source_text("\n\n".join(chapters))


def _tomato_save_root(session: requests.Session, base_url: str, headers: dict[str, str]) -> Path:
    env_value = os.environ.get(TOMATO_SAVE_DIR_ENV, "").strip()
    if env_value:
        return Path(env_value)

    try:
        response = session.get(f"{base_url}/api/library", params={"start": "false"}, headers=headers, timeout=8)
        if response.ok:
            root = _clean_text(str(response.json().get("root") or ""))
            if root:
                return Path(root)
    except Exception:
        pass

    try:
        response = session.get(f"{base_url}/api/config/full", headers=headers, timeout=8)
        if response.ok:
            save_path = _clean_text(str(response.json().get("save_path") or ""))
            if save_path:
                return Path(save_path)
    except Exception:
        pass

    return Path.cwd()


def _tomato_submit_waiting_choices(
    session: requests.Session,
    base_url: str,
    headers: dict[str, str],
    job: dict,
    submitted: set[str],
) -> None:
    job_id = job.get("id")
    if not job_id:
        return
    if job.get("book_name_options") and "book_name" not in submitted:
        try:
            session.post(f"{base_url}/api/jobs/{job_id}/book_name", json={"value": None}, headers=headers, timeout=8)
            submitted.add("book_name")
        except Exception:
            pass
    if job.get("format_options") and "format" not in submitted:
        try:
            session.post(f"{base_url}/api/jobs/{job_id}/format", json={"value": "txt"}, headers=headers, timeout=8)
            submitted.add("format")
        except Exception:
            pass


def _fetch_fanqie_chapters_via_tomato(
    source_url: str,
    *,
    log_callback=None,
    limit: int = QIDIAN_COVER_PROMPT_CHAPTER_COUNT,
) -> str:
    def log(level: str, message: str) -> None:
        if callable(log_callback):
            log_callback(level, message)

    base_url = _tomato_web_base_url()
    if not base_url:
        return ""

    book_id = _fanqie_book_id(source_url)
    if not book_id:
        return ""

    headers = _tomato_web_headers()
    session = requests.Session()
    try:
        status = session.get(f"{base_url}/api/status", headers=headers, timeout=2.5)
    except requests.RequestException as error:
        if os.environ.get(TOMATO_WEB_URL_ENV):
            log("INFO", f"Tomato: Web UI недоступен ({base_url}), пробую автозапуск: {error}")
        else:
            log("INFO", "Tomato: локальный Web UI не запущен, пробую автозапуск.")
        if not _start_tomato_web_server(session, base_url, headers, log_callback=log_callback):
            return ""
        try:
            status = session.get(f"{base_url}/api/status", headers=headers, timeout=8)
        except requests.RequestException as retry_error:
            log("WARNING", f"Tomato: Web UI не ответил после автозапуска: {retry_error}")
            return ""
    if status.status_code == 401:
        log("WARNING", f"Tomato: Web UI требует пароль; укажите {TOMATO_WEB_PASSWORD_ENV}.")
        return ""
    if not status.ok:
        log("WARNING", f"Tomato: Web UI вернул HTTP {status.status_code}, использую Playwright.")
        return ""

    log("INFO", f"Tomato: скачиваю первые {limit} главы Fanqie через локальный Web UI...")
    try:
        response = session.post(
            f"{base_url}/api/jobs",
            json={"book_id": source_url, "range_start": 1, "range_end": limit},
            headers=headers,
            timeout=12,
        )
        if response.status_code == 429:
            log("WARNING", "Tomato: уже есть активная задача, использую Playwright.")
            return ""
        if response.status_code == 401:
            log("WARNING", f"Tomato: Web UI требует пароль; укажите {TOMATO_WEB_PASSWORD_ENV}.")
            return ""
        response.raise_for_status()
        job_payload = response.json()
        job_id = job_payload.get("id")
        resolved_book_id = _clean_text(str(job_payload.get("book_id") or book_id))
        if not job_id:
            log("WARNING", "Tomato: Web UI не вернул id задачи, использую Playwright.")
            return ""
    except Exception as error:
        log("WARNING", f"Tomato: не удалось создать задачу загрузки: {error}")
        return ""

    submitted: set[str] = set()
    deadline = time.monotonic() + TOMATO_JOB_TIMEOUT_SECONDS
    last_state = ""
    while time.monotonic() < deadline:
        try:
            response = session.get(
                f"{base_url}/api/jobs",
                params={"id": job_id, "all": "true"},
                headers=headers,
                timeout=8,
            )
            response.raise_for_status()
            items = response.json().get("items") or []
        except Exception as error:
            log("WARNING", f"Tomato: не удалось получить статус задачи: {error}")
            return ""
        if not items:
            log("WARNING", "Tomato: задача пропала из списка, использую Playwright.")
            return ""

        job = items[0]
        _tomato_submit_waiting_choices(session, base_url, headers, job, submitted)
        state = str(job.get("state") or "")
        if state and state != last_state:
            last_state = state
            log("INFO", f"Tomato: состояние задачи {state}.")
        if state == "done":
            break
        if state in {"failed", "canceled"}:
            message = _clean_text(str(job.get("message") or state))
            log("WARNING", f"Tomato: задача завершилась неуспешно: {message}")
            return ""
        time.sleep(1.5)
    else:
        log("WARNING", "Tomato: задача не завершилась за отведённое время, использую Playwright.")
        return ""

    save_root = _tomato_save_root(session, base_url, headers)
    folder = _tomato_status_folder(save_root, resolved_book_id)
    chapters_text = _read_tomato_chapters_from_folder(folder, limit=limit)
    if not chapters_text:
        log("WARNING", f"Tomato: скачанные главы не найдены в {folder}, использую Playwright.")
        return ""
    log("SUCCESS", f"Tomato: главы для контекста обложки взяты из {folder}.")
    return chapters_text


def _truncate_cover_source_text(text: str, max_chars: int = QIDIAN_COVER_PROMPT_MAX_CHARS) -> str:
    text = _clean_multiline(text)
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "\n\n[Текст обрезан по лимиту контекста.]"


def build_cover_prompt_request(title_ru: str, chapters_text: str, original_description: str = "") -> str:
    title_ru = _clean_text(title_ru)
    original_description = _clean_multiline(original_description)
    chapters_text = _truncate_cover_source_text(chapters_text)
    return f"""Роль:
Ты — эксперт по промптингу для нейросетей нового поколения (DALL-E 3, Ideogram), которые умеют генерировать текст.
Твоя задача: на основе Названия, оригинального описания и Текста новеллы создать единый промт на английском, который опишет и визуал, и дизайн заголовка.

ВВОДНЫЕ ДАННЫЕ:
1. Название (RU): {title_ru}
2. Оригинальное описание источника:
{original_description or "[описание не найдено]"}
3. Текст первых глав:
{chapters_text}

ИНСТРУКЦИЯ:
1. Проанализируй жанр.
2. Опиши сцену (Герой + Фон).
3. ГЛАВНОЕ: Добавь блок с описанием Текста (Typography). Вставь русское название новеллы в кавычки внутри промта. Подбери шрифт под жанр (например: Horror = bloody font; Cyberpunk = neon glitch; Fantasy = golden 3D font).

СТРУКТУРА ОТВЕТА (Строго этот формат, один промпт на английском, без markdown и пояснений):
`[Subject & Action]`, `[Background & Atmosphere]`, `[Typography: The text "{title_ru}" written in [Font Style Description: e.g. massive 3D golden letters / grungy horror font / futuristic neon glitch font], placed at the [bottom/top], professional book cover typography, legible, high contrast]`, `[Visual Style: Manhwa style, Riot Games Splash Art, 8k, masterpiece]`, `--ar 2:3`

ПРИМЕР ТОГО, ЧТО ТЫ ДОЛЖЕН ВЫДАТЬ:
A dark necromancer raising skeletons, green fire aura, dark dungeon background. Typography: The text "ВЕК МЁРТВЫХ" written in massive bold rusted metal 3D font, jagged edges, glowing red outline, placed at the bottom center. Manhwa style, semi-realistic, cinematic lighting, --ar 2:3
"""


def clean_cover_prompt_response(raw_response: str) -> str:
    response = str(raw_response or "").strip()
    response = re.sub(r"^```(?:text|prompt)?\s*", "", response, flags=re.IGNORECASE)
    response = re.sub(r"\s*```$", "", response)
    return response.strip().strip("`").strip()


def _run_ai_request(
    *,
    provider_id: str,
    model_settings: dict,
    active_keys: list[str],
    settings_manager,
    prompt: str,
    log_callback,
    log_prefix: str,
    max_output_tokens: int = 4096,
) -> str:
    provider_config = deepcopy(api_config.api_providers().get(provider_id) or {})
    if not provider_config:
        raise ValueError(f"Провайдер '{provider_id}' не найден в конфиге.")

    model_name = model_settings.get("model") or api_config.default_model_name()
    model_config = deepcopy(api_config.all_models().get(model_name) or {})
    if not model_config:
        provider_models = provider_config.get("models") or {}
        model_config = deepcopy(provider_models.get(model_name) or {})
    if not model_config:
        raise ValueError(f"Модель '{model_name}' не найдена в конфиге провайдера.")

    model_config.setdefault("provider", provider_id)
    model_config.setdefault("id", model_config.get("model_id") or model_name)
    api_key = active_keys[0]

    worker = _SingleRequestWorker(
        settings_manager=settings_manager,
        provider_config=provider_config,
        model_config=model_config,
        api_key=api_key,
        model_settings=model_settings,
        log_callback=log_callback,
    )

    handler_class_name = provider_config.get("handler_class")
    if not handler_class_name:
        raise ValueError(f"У провайдера '{provider_id}' не указан handler_class.")

    handler_class = get_api_handler_class(handler_class_name)
    handler = handler_class(worker)
    client = SimpleNamespace(api_key=api_key)
    proxy_settings = settings_manager.load_proxy_settings() if settings_manager else None
    if not handler.setup_client(client_override=client, proxy_settings=proxy_settings):
        raise ValueError(f"Не удалось подготовить клиент провайдера '{provider_id}'.")

    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(
            handler.execute_api_call(
                prompt,
                log_prefix,
                allow_incomplete=False,
                debug=False,
                use_stream=True,
                max_output_tokens=max_output_tokens,
            )
        )
    finally:
        close_coro = getattr(handler, "_close_thread_session_internal", None)
        if callable(close_coro):
            try:
                loop.run_until_complete(close_coro())
            except Exception:
                pass
        loop.close()


class QidianFetchWorker(QThread):
    log_signal = pyqtSignal(str, str)
    metadata_ready = pyqtSignal(object)
    finished_signal = pyqtSignal()

    def __init__(self, qidian_url: str, visible_browser: bool = False):
        super().__init__()
        self.qidian_url = qidian_url.strip()
        self.visible_browser = visible_browser

    def log(self, level: str, message: str) -> None:
        self.log_signal.emit(level, message)

    def run(self) -> None:
        try:
            if not validate_source_url(self.qidian_url):
                raise ValueError(
                    "Введите ссылку вида https://www.qidian.com/book/1041604040/ "
                    "или https://fanqienovel.com/page/7229603492648717324"
                )

            configure_playwright_runtime()
            from playwright.sync_api import sync_playwright

            source = _source_name(self.qidian_url)
            self.log("INFO", f"{source}: открываю страницу книги...")
            with sync_playwright() as playwright:
                browser = _launch_chromium(
                    playwright,
                    headless=not self.visible_browser,
                    log_callback=self.log,
                )
                page = browser.new_page(
                    viewport={"width": 1280, "height": 900},
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/125.0.0.0 Safari/537.36"
                    ),
                )
                try:
                    page.goto(self.qidian_url, wait_until="domcontentloaded", timeout=60000)
                    if validate_fanqie_url(self.qidian_url):
                        try:
                            page.wait_for_function(
                                "() => window.__INITIAL_STATE__ && window.__INITIAL_STATE__.page && window.__INITIAL_STATE__.page.bookName",
                                timeout=12000,
                            )
                        except Exception:
                            page.wait_for_timeout(2500)
                        payload = page.evaluate(_FANQIE_EXTRACT_SCRIPT)
                    else:
                        try:
                            page.wait_for_function(
                                """() => {
                                    const body = document.body && document.body.innerText;
                                    return body && /(?:作品|内容|书籍|小说)(?:简介|介绍)/.test(body) && body.length > 1000;
                                }""",
                                timeout=12000,
                            )
                        except Exception:
                            page.wait_for_timeout(2500)
                        payload = page.evaluate(_QIDIAN_EXTRACT_SCRIPT)
                finally:
                    browser.close()

            title_original = _clean_text(payload.get("title"))
            author_name = _clean_text(payload.get("author"))
            cover_url = _normalize_url(payload.get("cover_url"), self.qidian_url)
            description = _clean_multiline(payload.get("description"))
            if not validate_fanqie_url(self.qidian_url):
                description = _select_qidian_description(
                    payload,
                    title=title_original,
                    author=author_name,
                )
            metadata = QidianBookMetadata(
                source_url=self.qidian_url,
                title_original=title_original,
                author_name=author_name,
                description=description,
                cover_url=cover_url,
                cover_image_data=_download_cover_image(cover_url, referer=self.qidian_url),
            )
            if not metadata.title_original:
                raise ValueError(f"{source}: не удалось определить название книги.")
            if not metadata.author_name:
                self.log("WARNING", f"{source}: автор не найден, его можно вписать вручную.")
            if not metadata.description:
                self.log("WARNING", f"{source}: описание не найдено, его можно вставить вручную.")
            if metadata.cover_url and not metadata.cover_image_data:
                self.log("WARNING", f"{source}: ссылка на обложку найдена, но предпросмотр загрузить не удалось.")
            self.metadata_ready.emit(metadata)
            self.log("SUCCESS", f"{source}: получено '{metadata.title_original}'.")
        except Exception as error:
            self.log("ERROR", f"Источник: {error}")
            self.log("DEBUG", traceback.format_exc())
        finally:
            self.finished_signal.emit()


class RulateLoginWorker(QThread):
    log_signal = pyqtSignal(str, str)
    finished_signal = pyqtSignal()

    def run(self) -> None:
        try:
            configure_playwright_runtime()
            from playwright.sync_api import sync_playwright

            self.log_signal.emit("INFO", "Rulate: открываю браузер для входа.")
            with sync_playwright() as playwright:
                browser = _launch_persistent_chromium_context(
                    playwright,
                    user_data_dir=str(RULATE_PROFILE_DIR),
                    viewport={"width": 1280, "height": 900},
                    log_callback=self.log_signal.emit,
                )
                page = browser.pages[0] if browser.pages else browser.new_page()
                try:
                    page.goto(RULATE_LOGIN_URL, timeout=60000)
                except Exception:
                    pass
                self.log_signal.emit(
                    "WARNING",
                    "Войдите в Rulate в открытом браузере и закройте окно браузера. Куки сохранятся.",
                )
                try:
                    while True:
                        page.wait_for_timeout(1000)
                except Exception:
                    pass
            self.log_signal.emit("SUCCESS", "Rulate: браузер закрыт, куки сохранены.")
        except Exception as error:
            self.log_signal.emit("ERROR", f"Rulate login: {error}")
            self.log_signal.emit("DEBUG", traceback.format_exc())
        finally:
            self.finished_signal.emit()


def _fetch_qidian_cover_context(
    qidian_url: str,
    *,
    visible_browser: bool = False,
    original_description: str = "",
    log_callback=None,
) -> tuple[str, str]:
    configure_playwright_runtime()
    from playwright.sync_api import sync_playwright

    def log(level: str, message: str) -> None:
        if callable(log_callback):
            log_callback(level, message)

    description = _clean_multiline(original_description)
    log("INFO", "Qidian: ищу первые главы для контекста обложки...")
    chapters = []
    with sync_playwright() as playwright:
        browser = _launch_chromium(
            playwright,
            headless=not visible_browser,
            log_callback=log,
        )
        page = browser.new_page(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
        )
        try:
            page.goto(qidian_url, wait_until="domcontentloaded", timeout=60000)
            try:
                page.wait_for_function(
                    "() => Array.from(document.querySelectorAll('a[href]')).some(a => a.href.includes('/chapter/'))",
                    timeout=12000,
                )
            except Exception:
                page.wait_for_timeout(3000)

            if not description:
                try:
                    payload = page.evaluate(_QIDIAN_EXTRACT_SCRIPT)
                    title_original = _clean_text(payload.get("title"))
                    author_name = _clean_text(payload.get("author"))
                    description = _select_qidian_description(
                        payload,
                        title=title_original,
                        author=author_name,
                    )
                    if description:
                        log("SUCCESS", "Qidian: описание добавлено в контекст обложки.")
                except Exception as error:
                    log("WARNING", f"Qidian: не удалось получить описание для контекста обложки: {error}")

            links = page.evaluate(_QIDIAN_CHAPTER_LINKS_SCRIPT, QIDIAN_COVER_PROMPT_CHAPTER_COUNT)
            if not links:
                raise ValueError("На странице книги не найдены ссылки на главы.")

            for index, link in enumerate(links[:QIDIAN_COVER_PROMPT_CHAPTER_COUNT], start=1):
                href = link.get("href") if isinstance(link, dict) else ""
                if not href:
                    continue
                title = _clean_text(link.get("title") if isinstance(link, dict) else "") or f"Глава {index}"
                log("INFO", f"Qidian: читаю {title}...")
                page.goto(href, wait_until="domcontentloaded", timeout=60000)
                try:
                    page.wait_for_selector(".content-text, main[id^='c-'], main.content", timeout=12000)
                except Exception:
                    page.wait_for_timeout(2500)
                payload = page.evaluate(_QIDIAN_CHAPTER_TEXT_SCRIPT)
                chapter_title = title or _clean_text(payload.get("title")) or f"Глава {index}"
                chapter_text = _clean_qidian_chapter_text(payload.get("text"))
                if not chapter_text:
                    log("WARNING", f"Qidian: текст главы '{title}' не найден, пропускаю.")
                    continue
                chapters.append(f"{chapter_title}\n{chapter_text}")
        finally:
            browser.close()

    log("SUCCESS", f"Qidian: получено глав для контекста обложки: {len(chapters)}.")
    return _truncate_cover_source_text("\n\n".join(chapters)), description


def _fetch_fanqie_cover_context(
    source_url: str,
    *,
    visible_browser: bool = False,
    original_description: str = "",
    log_callback=None,
) -> tuple[str, str]:
    def log(level: str, message: str) -> None:
        if callable(log_callback):
            log_callback(level, message)

    description = _clean_multiline(original_description)
    log("INFO", "Fanqie: ищу первые главы для контекста обложки...")
    tomato_chapters = _fetch_fanqie_chapters_via_tomato(
        source_url,
        log_callback=log_callback,
        limit=QIDIAN_COVER_PROMPT_CHAPTER_COUNT,
    )
    if tomato_chapters and description:
        log("SUCCESS", "Fanqie: получено глав для контекста обложки через Tomato.")
        return tomato_chapters, description

    configure_playwright_runtime()
    from playwright.sync_api import sync_playwright

    chapters = []
    with sync_playwright() as playwright:
        browser = _launch_chromium(
            playwright,
            headless=not visible_browser,
            log_callback=log,
        )
        page = browser.new_page(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
        )
        try:
            page.goto(source_url, wait_until="domcontentloaded", timeout=60000)
            try:
                page.wait_for_function(
                    "() => window.__INITIAL_STATE__ && window.__INITIAL_STATE__.page && window.__INITIAL_STATE__.page.bookName",
                    timeout=12000,
                )
            except Exception:
                page.wait_for_timeout(3000)

            payload = page.evaluate(_FANQIE_EXTRACT_SCRIPT)
            if not description:
                description = _clean_multiline(payload.get("description"))
                if description:
                    log("SUCCESS", "Fanqie: описание добавлено в контекст обложки.")

            if tomato_chapters:
                log("SUCCESS", "Fanqie: получено глав для контекста обложки через Tomato.")
                return tomato_chapters, description

            links = page.evaluate(_FANQIE_CHAPTER_LINKS_SCRIPT, QIDIAN_COVER_PROMPT_CHAPTER_COUNT)
            if not links:
                raise ValueError("На странице книги не найдены ссылки на главы.")

            for index, link in enumerate(links[:QIDIAN_COVER_PROMPT_CHAPTER_COUNT], start=1):
                href = link.get("href") if isinstance(link, dict) else ""
                if not href:
                    continue
                title = _clean_text(link.get("title") if isinstance(link, dict) else "") or f"Глава {index}"
                log("INFO", f"Fanqie: читаю {title}...")
                page.goto(href, wait_until="domcontentloaded", timeout=60000)
                try:
                    page.wait_for_selector(".muye-reader-content, .reader-content, article", timeout=12000)
                except Exception:
                    page.wait_for_timeout(2500)
                chapter_payload = page.evaluate(_FANQIE_CHAPTER_TEXT_SCRIPT)
                chapter_title = title or _clean_text(chapter_payload.get("title")) or f"Глава {index}"
                chapter_text = _clean_fanqie_chapter_text(chapter_payload.get("text"))
                if not chapter_text:
                    log(
                        "WARNING",
                        f"Fanqie: текст главы '{title}' не получен или обфусцирован, пропускаю.",
                    )
                    continue
                chapters.append(f"{chapter_title}\n{chapter_text}")
        finally:
            browser.close()

    log("SUCCESS", f"Fanqie: получено глав для контекста обложки: {len(chapters)}.")
    return _truncate_cover_source_text("\n\n".join(chapters)), description


def _fetch_source_cover_context(
    source_url: str,
    *,
    visible_browser: bool = False,
    original_description: str = "",
    log_callback=None,
) -> tuple[str, str]:
    if validate_fanqie_url(source_url):
        return _fetch_fanqie_cover_context(
            source_url,
            visible_browser=visible_browser,
            original_description=original_description,
            log_callback=log_callback,
        )
    return _fetch_qidian_cover_context(
        source_url,
        visible_browser=visible_browser,
        original_description=original_description,
        log_callback=log_callback,
    )


class AiPrepareWorker(QThread):
    log_signal = pyqtSignal(str, str)
    prepared_ready = pyqtSignal(object)
    finished_signal = pyqtSignal()

    def __init__(
        self,
        metadata: QidianBookMetadata,
        provider_id: str,
        model_settings: dict,
        active_keys: list[str],
        settings_manager,
        *,
        visible_browser: bool = False,
    ):
        super().__init__()
        self.metadata = metadata
        self.provider_id = provider_id
        self.model_settings = model_settings or {}
        self.active_keys = active_keys or []
        self.settings_manager = settings_manager
        self.visible_browser = visible_browser

    def log(self, level: str, message: str) -> None:
        self.log_signal.emit(level, message)

    def run(self) -> None:
        try:
            english_title = ""
            try:
                self.log("INFO", "Google Translate: перевожу название на английский...")
                english_title = google_translate_title_to_english(self.metadata.title_original)
                self.log("SUCCESS", f"Google Translate: {english_title}")
            except Exception as error:
                self.log("WARNING", f"Google Translate недоступен, английское название попросим у AI: {error}")

            if not self.provider_id:
                raise ValueError("Не выбран AI-сервис.")
            if not self.active_keys:
                raise ValueError("Не выбран активный ключ или сессия для AI-сервиса.")

            self.log("INFO", "AI: перевожу название и описание...")
            translation_prompt = build_ai_prompt(self.metadata, english_title)
            translation_response = self._run_ai_request(
                translation_prompt,
                log_prefix="Qidian -> Rulate translation",
                max_output_tokens=2048,
            )
            prepared = parse_translation_metadata(translation_response)
            if english_title and not prepared.english_title:
                prepared.english_title = english_title
            if not prepared.translated_title:
                raise ValueError("AI не вернул название на русском.")
            if not prepared.translated_description:
                raise ValueError("AI не вернул описание на русском.")

            chapters_text = ""
            if validate_source_url(self.metadata.source_url):
                try:
                    source = _source_name(self.metadata.source_url)
                    chapters_text, original_description = _fetch_source_cover_context(
                        self.metadata.source_url,
                        visible_browser=self.visible_browser,
                        original_description=self.metadata.description,
                        log_callback=self.log,
                    )
                    if original_description and not self.metadata.description:
                        self.metadata.description = original_description
                except Exception as error:
                    self.log("WARNING", f"{source}: главы для промпта обложки не получены, продолжаю без них: {error}")
            else:
                self.log("WARNING", "Источник: ссылка на оригинал не задана, промпт обложки будет без текста глав.")

            self.log("INFO", "AI: подбираю жанры, теги и промпт обложки...")
            catalog_prompt = build_catalog_prompt(self.metadata, prepared, chapters_text)
            catalog_response = self._run_ai_request(
                catalog_prompt,
                log_prefix="Qidian -> Rulate catalog",
                max_output_tokens=4096,
            )
            catalog = parse_catalog_metadata(catalog_response)
            prepared.genres = catalog.genres
            prepared.tags = catalog.tags
            prepared.cover_prompt = catalog.cover_prompt
            if not prepared.cover_prompt:
                self.log("WARNING", "AI не вернул промпт для обложки, его можно сгенерировать отдельной кнопкой.")

            self.prepared_ready.emit(prepared)
            self.log("SUCCESS", "AI: данные для Rulate подготовлены.")
        except Exception as error:
            self.log("ERROR", f"AI: {error}")
            self.log("DEBUG", traceback.format_exc())
        finally:
            self.finished_signal.emit()

    def _run_ai_request(
        self,
        prompt: str,
        *,
        log_prefix: str = "Qidian -> Rulate",
        max_output_tokens: int = 4096,
    ) -> str:
        return _run_ai_request(
            provider_id=self.provider_id,
            model_settings=self.model_settings,
            active_keys=self.active_keys,
            settings_manager=self.settings_manager,
            prompt=prompt,
            log_callback=self.log,
            log_prefix=log_prefix,
            max_output_tokens=max_output_tokens,
        )


class CoverPromptWorker(QThread):
    log_signal = pyqtSignal(str, str)
    prompt_ready = pyqtSignal(str)
    finished_signal = pyqtSignal()

    def __init__(
        self,
        qidian_url: str,
        title_ru: str,
        provider_id: str,
        model_settings: dict,
        active_keys: list[str],
        settings_manager,
        *,
        original_description: str = "",
        visible_browser: bool = False,
    ):
        super().__init__()
        self.qidian_url = qidian_url.strip()
        self.title_ru = title_ru.strip()
        self.original_description = _clean_multiline(original_description)
        self.provider_id = provider_id
        self.model_settings = model_settings or {}
        self.active_keys = active_keys or []
        self.settings_manager = settings_manager
        self.visible_browser = visible_browser

    def log(self, level: str, message: str) -> None:
        self.log_signal.emit(level, message)

    def run(self) -> None:
        try:
            if not validate_source_url(self.qidian_url):
                raise ValueError(
                    "Введите ссылку вида https://www.qidian.com/book/1041604040/ "
                    "или https://fanqienovel.com/page/7229603492648717324"
                )
            if not self.title_ru:
                raise ValueError("Заполните русское название перед генерацией промпта обложки.")
            if not self.provider_id:
                raise ValueError("Не выбран AI-сервис.")
            if not self.active_keys:
                raise ValueError("Не выбран активный ключ или сессия для AI-сервиса.")

            chapters_text = self._fetch_chapters_text()
            if not chapters_text:
                self.log("WARNING", "Источник: текст первых глав не получен, генерирую промпт по описанию.")

            prompt = build_cover_prompt_request(
                self.title_ru,
                chapters_text,
                original_description=self.original_description,
            )
            self.log("INFO", "AI: генерирую промпт для обложки...")
            raw_response = _run_ai_request(
                provider_id=self.provider_id,
                model_settings=self.model_settings,
                active_keys=self.active_keys,
                settings_manager=self.settings_manager,
                prompt=prompt,
                log_callback=self.log,
                log_prefix="Qidian cover prompt",
                max_output_tokens=2048,
            )
            cover_prompt = clean_cover_prompt_response(raw_response)
            if not cover_prompt:
                raise ValueError("AI не вернул промпт для обложки.")
            self.prompt_ready.emit(cover_prompt)
            self.log("SUCCESS", "AI: промпт для обложки готов.")
        except Exception as error:
            self.log("ERROR", f"Cover prompt: {error}")
            self.log("DEBUG", traceback.format_exc())
        finally:
            self.finished_signal.emit()

    def _fetch_chapters_text(self) -> str:
        chapters_text, original_description = _fetch_source_cover_context(
            self.qidian_url,
            visible_browser=self.visible_browser,
            original_description=self.original_description,
            log_callback=self.log,
        )
        self.original_description = original_description
        return chapters_text


class RulateFillWorker(QThread):
    log_signal = pyqtSignal(str, str)
    finished_signal = pyqtSignal()

    def __init__(self, draft: RulateBookDraft):
        super().__init__()
        self.draft = draft

    def log(self, level: str, message: str) -> None:
        self.log_signal.emit(level, message)

    def run(self) -> None:
        try:
            configure_playwright_runtime()
            from playwright.sync_api import sync_playwright

            self.log("INFO", "Rulate: открываю форму создания книги...")
            with sync_playwright() as playwright:
                browser = _launch_persistent_chromium_context(
                    playwright,
                    user_data_dir=str(RULATE_PROFILE_DIR),
                    viewport={"width": 1280, "height": 900},
                    log_callback=self.log,
                )
                page = browser.pages[0] if browser.pages else browser.new_page()
                form_opened = self._select_catalog_category(page)
                if not form_opened:
                    self.log("WARNING", "Rulate: форма после выбора раздела не открылась, пробую открыть общую информацию напрямую.")
                    page.goto(RULATE_INFO_URL, timeout=60000)
                    page.wait_for_timeout(1500)

                if page.locator("#form-edit").count() == 0:
                    self.log(
                        "WARNING",
                        "Форма не найдена. Если открыта страница входа, войдите в Rulate и повторите заполнение.",
                    )
                    page.wait_for_timeout(60000)
                    return

                self._fill_general(page)
                self._fill_description(page)
                self.log(
                    "SUCCESS",
                    "Rulate: форма заполнена. Проверьте вкладки и нажмите сохранение вручную.",
                )
                try:
                    while True:
                        page.wait_for_timeout(1000)
                except Exception:
                    pass
        except Exception as error:
            self.log("ERROR", f"Rulate: {error}")
            self.log("DEBUG", traceback.format_exc())
        finally:
            self.finished_signal.emit()

    def _select_catalog_category(self, page) -> bool:
        self.log("INFO", "Rulate: открываю выбор раздела каталога...")
        page.goto(RULATE_CATEGORY_URL, timeout=60000)
        page.wait_for_timeout(1500)

        try:
            page.locator(RULATE_BOOK_TYPE_SELECTOR).first.click(timeout=10000)
        except Exception:
            self.log("WARNING", "Rulate: не удалось автоматически выбрать тип 'Книга'. Проверьте страницу категории вручную.")
            return False

        try:
            page.wait_for_load_state("domcontentloaded", timeout=10000)
        except Exception:
            pass
        page.wait_for_timeout(1200)
        self.log("SUCCESS", "Rulate: тип 'Книга' выбран.")

        selected = page.evaluate(
            """(categoryTitle) => {
                const normalize = (value) => (value || "").replace(/\\s+/g, " ").trim();
                const clickCandidate = (selector) => {
                    const candidates = Array.from(document.querySelectorAll(selector));
                    for (const candidate of candidates) {
                        if (candidate.tagName === "A") {
                            const href = candidate.getAttribute("href") || "";
                            if (href && href !== "#" && !href.includes("/book/0/edit/cat")) {
                                continue;
                            }
                        }
                        if (normalize(candidate.textContent) === categoryTitle) {
                            candidate.click();
                            return true;
                        }
                    }
                    return false;
                };
                if (clickCandidate("a, button, label")) return true;

                const inputs = Array.from(document.querySelectorAll("input[type=radio], input[type=checkbox]"));
                for (const input of inputs) {
                    const id = input.getAttribute("id");
                    const label = id ? document.querySelector(`label[for="${CSS.escape(id)}"]`) : null;
                    const parentText = normalize(input.closest("label, li, div")?.textContent);
                    if (normalize(label?.textContent) === categoryTitle || parentText === categoryTitle) {
                        input.checked = true;
                        input.dispatchEvent(new Event("input", {bubbles: true}));
                        input.dispatchEvent(new Event("change", {bubbles: true}));
                        input.click();
                        return true;
                    }
                }
                return false;
            }""",
            RULATE_CHINESE_CATEGORY_TITLE,
        )
        if not selected:
            self.log("WARNING", "Rulate: не удалось автоматически выбрать раздел 'Китайские'. Проверьте страницу категории вручную.")
            return False

        try:
            page.wait_for_load_state("domcontentloaded", timeout=10000)
        except Exception:
            pass
        try:
            page.locator("#form-edit").wait_for(state="attached", timeout=20000)
        except Exception:
            self.log("WARNING", "Rulate: раздел 'Китайские' выбран, но форма редактирования пока не найдена.")
            return False

        self.log("SUCCESS", "Rulate: раздел каталога 'Китайские' выбран, форма открыта.")
        return True

    def _fill_general(self, page) -> None:
        qidian = self.draft.qidian
        prepared = self.draft.prepared
        _show_rulate_tab(page, "general")
        page.select_option("#Book_s_lang", "7")
        page.select_option("#Book_t_lang", "1")
        _fill(page, "#Book_s_title", prepared.english_title)
        _fill(page, "#Book_t_title", prepared.translated_title)
        _fill(page, "#Book_author", qidian.author_name)
        _fill(page, "#Book_source_url", qidian.source_url)
        _fill(page, "#Book_a_title_1", qidian.title_original)
        translator_team_mode = getattr(prepared, "translator_team_mode", "")
        if translator_team_mode == "first_suggestion" and not _select_first_rulate_choice_field(
            page,
            selectors=(
                '[name="Book[team_id]"]',
                '[name="Book[team_ids][]"]',
                '[name="Book[teams][]"]',
                '[name="Book[teams]"]',
                '[name="Book[team]"]',
                '[name="Book[translator_team]"]',
                '[name="Book[translator_team_id]"]',
                '[name="Book[translation_team]"]',
                '[name="Book[translation_team_id]"]',
                '[name="Book[translate_group]"]',
                '[name="Book[group]"]',
                '[name="Book[group_id]"]',
                "#Book_teams",
                "#Book_team",
                "#Book_team_id",
                "#Book_team_ids",
                "#Book_translate_team",
                "#Book_translation_team",
                "#Book_translation_team_id",
                "#Book_translator_team",
                "#Book_translator_team_id",
                "#Book_translate_group",
                "#Book_group",
                "#Book_group_id",
            ),
            labels=(
                "Команда переводчиков",
                "Команда перевода",
                "Группа переводчиков",
                "Группа перевода",
                "Команда",
                "Переводчики",
                "Translator team",
                "Translation team",
            ),
        ):
            self.log("WARNING", "Rulate: первую подсказку команды переводчиков нужно выбрать вручную.")
        elif translator_team_mode == "first_suggestion":
            self.log("SUCCESS", "Rulate: выбрана первая подсказка команды переводчиков.")

    def _fill_description(self, page) -> None:
        prepared = self.draft.prepared
        _show_rulate_tab(page, "description")
        page.select_option('select[name="Book[status]"]', "1")
        page.evaluate(
            """(description) => {
                const textarea = document.querySelector("#Book_descr");
                if (textarea) {
                    textarea.value = description;
                    textarea.dispatchEvent(new Event("input", {bubbles: true}));
                    textarea.dispatchEvent(new Event("change", {bubbles: true}));
                }
                if (window.CKEDITOR && CKEDITOR.instances && CKEDITOR.instances.Book_descr) {
                    CKEDITOR.instances.Book_descr.setData(description);
                }
            }""",
            prepared.translated_description,
        )
        for genre in prepared.genres[:5]:
            if not _select_magic_value(page, "#Book_genres", genre, allow_free=False):
                self.log("WARNING", f"Rulate: жанр '{genre}' не найден в форме и пропущен.")
        for tag in prepared.tags[:8]:
            if not _select_magic_value(page, "#Book_tags", tag, allow_free=False):
                self.log("WARNING", f"Rulate: тег '{tag}' не найден в форме и пропущен.")


class _PromptBuilder:
    system_instruction = None


class _SingleRequestWorker:
    def __init__(
        self,
        *,
        settings_manager,
        provider_config: dict,
        model_config: dict,
        api_key: str,
        model_settings: dict,
        log_callback,
    ):
        self.settings_manager = settings_manager
        self.provider_config = provider_config
        self.model_config = model_config
        self.api_key = api_key
        self.model_id = model_config.get("id")
        self.prompt_builder = _PromptBuilder()
        self.system_instruction = None
        self.is_cancelled = False
        self.sync_executor = None
        self.temperature = model_settings.get("temperature")
        self.temperature_override_enabled = model_settings.get("temperature_override_enabled", False)
        self.thinking_enabled = model_settings.get("thinking_enabled", False)
        self.thinking_budget = model_settings.get("thinking_budget")
        self.thinking_level = model_settings.get("thinking_level")
        self.max_concurrent_requests = model_settings.get("max_concurrent_requests") or 1
        self.workascii_workspace_name = model_settings.get("workascii_workspace_name", "")
        self.workascii_workspace_index = model_settings.get("workascii_workspace_index", 0)
        self.workascii_timeout_sec = model_settings.get("workascii_timeout_sec", 900)
        self.workascii_headless = model_settings.get("workascii_headless", False)
        self.workascii_profile_template_dir = model_settings.get("workascii_profile_template_dir", "")
        self.workascii_refresh_every_requests = model_settings.get("workascii_refresh_every_requests", 0)
        self.debug_logging_enabled = model_settings.get("debug_logging_enabled", False)
        self.debug_operation_filters = model_settings.get("debug_operation_filters", "")
        self.debug_max_log_mb = model_settings.get("debug_max_log_mb", 128)
        self._log_callback = log_callback

    def _post_event(self, event: str, data: dict | None = None) -> None:
        message = (data or {}).get("message") if isinstance(data, dict) else None
        if message:
            self._log_callback("INFO", str(message))

    def get_debug_operation_context(self) -> dict:
        return {"feature": "qidian_rulate"}


def _fill(page, selector: str, value: str) -> None:
    value = value or ""
    locator = page.locator(selector)
    locator.wait_for(state="attached", timeout=15000)
    locator.fill(value)
    page.evaluate(
        """([selector, value]) => {
            const element = document.querySelector(selector);
            if (!element) return;
            element.value = value;
            element.dispatchEvent(new Event("input", {bubbles: true}));
            element.dispatchEvent(new Event("change", {bubbles: true}));
        }""",
        [selector, value],
    )


def _selector_exists(page, selector: str) -> bool:
    try:
        return page.locator(selector).count() > 0
    except Exception:
        return False


def _wait_for_selector_attached(page, selector: str, timeout: int = 15000) -> bool:
    try:
        page.wait_for_selector(selector, state="attached", timeout=timeout)
        return True
    except Exception:
        try:
            page.locator(selector).first().wait_for(state="attached", timeout=timeout)
            return True
        except Exception:
            return False


def _select_first_plain_choice(page, selector: str) -> bool:
    try:
        return bool(
            page.evaluate(
                """(selector) => {
                    const element = document.querySelector(selector);
                    if (!element) return false;
                    const normalize = (text) => String(text || "").replace(/\\s+/g, " ").trim().toLowerCase();
                    const marker = (node) => normalize([
                        node.id,
                        node.getAttribute?.("name"),
                        node.getAttribute?.("class"),
                    ].filter(Boolean).join(" "));
                    const likelyTeamField = (node) => /team|translat|group|команд|групп|перевод/.test(marker(node));
                    const isKnownNonTeamField = (node) => /^Book\\[(?:status|s_lang|t_lang)\\]$/.test(
                        String(node.getAttribute?.("name") || "")
                    );
                    const setNativeValue = (el, nextValue) => {
                        const prototype = Object.getPrototypeOf(el);
                        const descriptor = Object.getOwnPropertyDescriptor(prototype, "value")
                            || Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")
                            || Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")
                            || Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value");
                        if (descriptor && descriptor.set) descriptor.set.call(el, nextValue);
                        else el.value = nextValue;
                        el.dispatchEvent(new Event("input", {bubbles: true}));
                        el.dispatchEvent(new Event("change", {bubbles: true}));
                    };
                    const selectFirstOption = (select) => {
                        if (!select || select.tagName !== "SELECT") return false;
                        if (isKnownNonTeamField(select) || !likelyTeamField(select)) return false;
                        const options = Array.from(select.options || []).filter((item) => !item.disabled);
                        const option = options.find((item) => String(item.value || "").trim())
                            || options.find((item) => {
                                const text = String(item.textContent || "").trim().toLowerCase();
                                return text && !text.includes("выбер") && !text.includes("select");
                            });
                        if (!option) return false;
                        setNativeValue(select, option.value);
                        if (window.jQuery) {
                            window.jQuery(select).val(option.value).trigger("change");
                        }
                        return true;
                    };

                    if (selectFirstOption(element)) return true;

                    const roots = [element, element.parentElement, element.previousElementSibling, element.nextElementSibling]
                        .filter(Boolean);
                    const closestGroup = element.closest?.(".form-group, .control-group, .form-row, .row, tr");
                    if (closestGroup) roots.push(closestGroup);
                    for (const root of roots) {
                        const select = root.querySelector?.("select");
                        if (selectFirstOption(select)) return true;
                    }

                    if (window.jQuery) {
                        for (const root of roots) {
                            const data = window.jQuery(root).data?.() || {};
                            if (data.select2 || data.chosen || data.selectize) {
                                const select = root.matches?.("select") ? root : root.querySelector?.("select");
                                if (selectFirstOption(select)) return true;
                                try {
                                    window.jQuery(root).trigger("mousedown").trigger("click");
                                } catch (_error) {}
                            }
                        }
                    }

                    const selectedInput = roots
                        .flatMap((root) => Array.from(root.querySelectorAll?.("input[type='hidden'], input[type='text']") || []))
                        .find((input) => likelyTeamField(input) && normalize(input.value));
                    if (selectedInput) return true;

                    return false;
                }""",
                selector,
            )
        )
    except Exception:
        return False


def _find_rulate_choice_selector_by_label(page, labels: tuple[str, ...]) -> str:
    try:
        return str(
            page.evaluate(
                """(labels) => {
                    const normalize = (text) => String(text || "").replace(/\\s+/g, " ").trim();
                    const marker = (node) => normalize(
                        [
                            node.id,
                            node.getAttribute?.("name"),
                            node.getAttribute?.("class"),
                            node.getAttribute?.("data-select2-id"),
                            node.getAttribute?.("aria-labelledby"),
                        ].filter(Boolean).join(" ")
                    ).toLowerCase();
                    const likelyTeamField = (node) => /team|translat|group|команд|групп|перевод/.test(marker(node));
                    const isKnownNonTeamField = (node) => /^Book\\[(?:status|s_lang|t_lang)\\]$/.test(
                        String(node.getAttribute?.("name") || "")
                    );
                    const selectorFor = (candidate) => {
                        if (candidate.id) {
                            const idSelector = `#${CSS.escape(candidate.id)}`;
                            if (document.querySelectorAll(idSelector).length === 1) return idSelector;
                        }
                        const markerValue = `choice-${Date.now()}-${Math.random().toString(16).slice(2)}`;
                        candidate.setAttribute("data-codex-rulate-choice", markerValue);
                        return `[data-codex-rulate-choice="${markerValue}"]`;
                    };
                    const candidateScore = (candidate) => {
                        if (candidate.disabled || isKnownNonTeamField(candidate)) return -100;
                        const name = String(candidate.getAttribute?.("name") || "").toLowerCase();
                        let score = 0;
                        if (/team|translat|group/.test(name)) score += 20;
                        if (likelyTeamField(candidate)) score += 5;
                        if (candidate.matches?.(".ms-ctn, .ms-parent, .magic-suggest, .select2-container, .selectize-control, .chosen-container")) {
                            score += 4;
                        }
                        if (candidate.matches?.("input[type='hidden']") && !likelyTeamField(candidate)) score -= 20;
                        return score;
                    };
                    const needles = labels.map((label) => normalize(label).toLowerCase()).filter(Boolean);
                    if (!needles.length) return "";
                    const groups = Array.from(document.querySelectorAll(".form-group, .control-group, .form-row, .row, tr"));
                    for (const group of groups) {
                        const labelText = normalize(
                            group.querySelector("label, .control-label, .form-label, th, td:first-child")?.textContent
                            || group.textContent
                            || ""
                        ).toLowerCase();
                        if (!needles.some((needle) => labelText.includes(needle))) continue;
                        const candidates = [
                            ...Array.from(group.querySelectorAll(
                                ".ms-ctn, .ms-parent, .magic-suggest, .select2-container, .selectize-control, .chosen-container"
                            )),
                            ...Array.from(group.querySelectorAll("select, textarea, input")),
                            ...Array.from(group.querySelectorAll("[data-select2-id], [aria-controls], [id]")),
                        ];
                        const ranked = candidates
                            .map((candidate) => [candidate, candidateScore(candidate)])
                            .filter((entry) => entry[1] >= 0)
                            .sort((left, right) => right[1] - left[1]);
                        for (const [candidate] of ranked) {
                            return selectorFor(candidate);
                        }
                    }
                    return "";
                }""",
                list(labels),
            )
            or ""
        )
    except Exception:
        return ""


def _select_first_rulate_choice_field(
    page,
    *,
    selectors: tuple[str, ...],
    labels: tuple[str, ...] = (),
) -> bool:
    label_selector = _find_rulate_choice_selector_by_label(page, labels)
    candidates = []
    if label_selector:
        candidates.append(label_selector)
    candidates.extend(selector for selector in selectors if selector not in candidates)

    for selector in candidates:
        if not _selector_exists(page, selector):
            continue
        if _select_first_magic_value(page, selector):
            return True
        if _select_first_plain_choice(page, selector):
            return True
    return False


def _show_rulate_tab(page, tab_id: str) -> None:
    page.evaluate(
        """(tabId) => {
            const selector = `a[href="#${tabId}"]`;
            const link = document.querySelector(selector);
            if (window.jQuery && link && window.jQuery.fn && window.jQuery.fn.tab) {
                window.jQuery(link).tab("show");
            } else if (link) {
                link.click();
            }
            const pane = document.getElementById(tabId);
            if (pane) {
                pane.classList.add("active");
                pane.style.display = "";
            }
        }""",
        tab_id,
    )
    try:
        page.locator(f"#{tab_id}").wait_for(state="visible", timeout=5000)
    except Exception:
        page.wait_for_timeout(500)


def _select_magic_value(page, selector: str, value: str, *, allow_free: bool) -> bool:
    value = (value or "").strip()
    if not value:
        return True

    if not _wait_for_selector_attached(page, selector, timeout=15000):
        return False
    selected = False
    for _ in range(20):
        selected = page.evaluate(_MAGIC_SELECT_SCRIPT, [selector, value, allow_free])
        if selected:
            return True
        page.wait_for_timeout(250)

    activated = page.evaluate(_MAGIC_TYPE_SCRIPT, [selector, value])
    if not activated:
        return False
    page.wait_for_timeout(500)

    selected = page.evaluate(
        """([selector, value, allowFree]) => {
            const normalize = (text) => String(text || "").replace(/\\s+/g, " ").trim().toLowerCase();
            const root = document.querySelector(selector);
            const containers = root ? [root] : [];
            const items = containers
                .flatMap(container => Array.from(container.querySelectorAll(".ms-res-item")))
                .concat(Array.from(document.querySelectorAll(".ms-res-item")));
            for (const item of items) {
                if (normalize(item.textContent) !== normalize(value)) continue;
                item.dispatchEvent(new MouseEvent("mousedown", {bubbles: true, cancelable: true, view: window}));
                item.click();
                item.dispatchEvent(new MouseEvent("mouseup", {bubbles: true, cancelable: true, view: window}));
                return true;
            }
            if (!allowFree || !root) return false;
            const input = root.querySelector(".ms-sel-ctn input, input[type='text']");
            if (!input) return false;
            input.dispatchEvent(new KeyboardEvent("keydown", {key: "Enter", code: "Enter", bubbles: true}));
            input.dispatchEvent(new KeyboardEvent("keyup", {key: "Enter", code: "Enter", bubbles: true}));
            return true;
        }""",
        [selector, value, allow_free],
    )
    return bool(selected)


def _select_first_magic_value(page, selector: str) -> bool:
    if not _wait_for_selector_attached(page, selector, timeout=15000):
        return False
    for _ in range(20):
        selected = page.evaluate(_MAGIC_SELECT_FIRST_SCRIPT, selector)
        if selected:
            return True
        page.wait_for_timeout(250)

    activated = page.evaluate(_MAGIC_OPEN_SCRIPT, selector)
    if not activated:
        return False
    for _ in range(12):
        page.wait_for_timeout(250)
        if page.evaluate(_MAGIC_CLICK_FIRST_SCRIPT, selector):
            return True
    return False


_MAGIC_SELECT_SCRIPT = """([selector, value, allowFree]) => {
    const normalize = (text) => String(text || "").replace(/\\s+/g, " ").trim().toLowerCase();
    const root = document.querySelector(selector);
    if (!root || !window.jQuery) return false;

    const candidates = [root, root.parentElement, root.previousElementSibling, root.nextElementSibling]
        .filter(Boolean);
    const closestGroup = root.closest?.(".form-group, .control-group, .form-row, .row, tr");
    if (closestGroup) candidates.push(closestGroup);
    for (const child of Array.from(root.querySelectorAll("*"))) {
        candidates.push(child);
    }
    if (closestGroup) {
        for (const child of Array.from(closestGroup.querySelectorAll("*"))) {
            candidates.push(child);
        }
    }

    let api = null;
    for (const node of candidates) {
        const data = window.jQuery(node).data() || {};
        api = data.magicSuggest || data.magicsuggest || data.ms || data.magic_suggest || null;
        if (!api) {
            api = Object.values(data).find(candidate => (
                candidate && (
                    typeof candidate.setValue === "function" ||
                    typeof candidate.setSelection === "function"
                )
            )) || null;
        }
        if (api && (typeof api.setValue === "function" || typeof api.setSelection === "function")) break;
    }
    if (!api) return false;

    const displayField = (api.settings && api.settings.displayField) || "name";
    const valueField = (api.settings && api.settings.valueField) || displayField;
    const rawData = typeof api.getData === "function"
        ? api.getData()
        : ((api.settings && api.settings.data) || []);
    const items = Array.isArray(rawData) ? rawData : Object.values(rawData || {});
    const matched = items.find(item => {
        const display = item && item[displayField] != null ? String(item[displayField]) : "";
        const name = item && item.name != null ? String(item.name) : "";
        const title = item && item.title != null ? String(item.title) : "";
        const id = item && item.id != null ? String(item.id) : "";
        return [display, name, title, id].some(candidate => normalize(candidate) === normalize(value));
    });

    if (matched && typeof api.setSelection === "function") {
        const currentSelection = typeof api.getSelection === "function" ? api.getSelection() : [];
        const selection = Array.isArray(currentSelection) ? currentSelection.slice() : [];
        const matchedKey = matched[valueField] != null ? matched[valueField] : (matched.id != null ? matched.id : value);
        const hasMatched = selection.some(item => {
            const itemKey = item && item[valueField] != null ? item[valueField] : (item && item.id != null ? item.id : item);
            return normalize(itemKey) === normalize(matchedKey);
        });
        if (!hasMatched) selection.push(matched);
        api.setSelection(selection);
        return true;
    }

    let selectedValue = value;
    if (matched && matched[valueField] != null) {
        selectedValue = matched[valueField];
    } else if (matched && matched.id != null) {
        selectedValue = matched.id;
    } else if (!allowFree) {
        return false;
    }

    if (typeof api.setValue !== "function") return false;
    const current = typeof api.getValue === "function" ? api.getValue() : [];
    const next = Array.isArray(current) ? current.slice() : [];
    if (!next.some(item => normalize(item) === normalize(selectedValue))) {
        next.push(selectedValue);
    }
    api.setValue(next);
    return true;
}"""


_MAGIC_SELECT_FIRST_SCRIPT = """(selector) => {
    const normalize = (text) => String(text || "").replace(/\\s+/g, " ").trim().toLowerCase();
    const root = document.querySelector(selector);
    if (!root || !window.jQuery) return false;
    const notifyRootChanged = () => {
        root.dispatchEvent(new Event("input", {bubbles: true}));
        root.dispatchEvent(new Event("change", {bubbles: true}));
        window.jQuery(root).trigger("change");
    };

    const candidates = [root, root.parentElement, root.previousElementSibling, root.nextElementSibling]
        .filter(Boolean);
    const closestGroup = root.closest?.(".form-group, .control-group, .form-row, .row, tr");
    if (closestGroup) candidates.push(closestGroup);
    for (const child of Array.from(root.querySelectorAll("*"))) {
        candidates.push(child);
    }
    if (closestGroup) {
        for (const child of Array.from(closestGroup.querySelectorAll("*"))) {
            candidates.push(child);
        }
    }

    let api = null;
    for (const node of candidates) {
        const data = window.jQuery(node).data() || {};
        api = data.magicSuggest || data.magicsuggest || data.ms || data.magic_suggest || null;
        if (!api) {
            api = Object.values(data).find(candidate => (
                candidate && (
                    typeof candidate.setValue === "function" ||
                    typeof candidate.setSelection === "function"
                )
            )) || null;
        }
        if (api && (typeof api.setValue === "function" || typeof api.setSelection === "function")) break;
    }
    if (!api) return false;

    const currentSelection = typeof api.getSelection === "function" ? api.getSelection() : [];
    if (Array.isArray(currentSelection) && currentSelection.length > 0) return true;
    const currentValue = typeof api.getValue === "function" ? api.getValue() : [];
    if (Array.isArray(currentValue) && currentValue.length > 0) return true;

    const displayField = (api.settings && api.settings.displayField) || "name";
    const valueField = (api.settings && api.settings.valueField) || displayField;
    const rawData = typeof api.getData === "function"
        ? api.getData()
        : ((api.settings && api.settings.data) || []);
    const items = Array.isArray(rawData) ? rawData : Object.values(rawData || {});
    const first = items.find(item => {
        if (!item) return false;
        const display = item[displayField] != null ? String(item[displayField]) : "";
        const name = item.name != null ? String(item.name) : "";
        const title = item.title != null ? String(item.title) : "";
        const id = item.id != null ? String(item.id) : "";
        return [display, name, title, id].some(candidate => normalize(candidate));
    });
    if (!first) return false;

    if (typeof api.setSelection === "function") {
        api.setSelection([first]);
        notifyRootChanged();
        return true;
    }

    if (typeof api.setValue !== "function") return false;
    const selectedValue = first[valueField] != null
        ? first[valueField]
        : (first.id != null ? first.id : first[displayField]);
    if (selectedValue == null || !normalize(selectedValue)) return false;
    api.setValue([selectedValue]);
    notifyRootChanged();
    return true;
}"""


_MAGIC_OPEN_SCRIPT = """(selector) => {
    const root = document.querySelector(selector);
    if (!root) return false;
    const nodes = [root, root.parentElement, root.previousElementSibling, root.nextElementSibling]
        .filter(Boolean);
    const closestGroup = root.closest?.(".form-group, .control-group, .form-row, .row, tr");
    if (closestGroup) nodes.push(closestGroup);
    for (const child of Array.from(root.querySelectorAll("*"))) {
        nodes.push(child);
    }
    if (closestGroup) {
        for (const child of Array.from(closestGroup.querySelectorAll("*"))) {
            nodes.push(child);
        }
    }
    const container = nodes.find(node => (
        node.matches?.(".ms-ctn, .ms-parent, .magic-suggest, .select2-container, .selectize-control, .chosen-container")
        || node.querySelector?.(
            ".ms-sel-ctn input, .ms-trigger, .select2-selection, .selectize-input, .chosen-single, .chosen-choices"
        )
    ));
    if (!container) return false;
    container.scrollIntoView({block: "center", inline: "nearest"});
    const clickTarget = container.querySelector?.(
        ".ms-trigger, .ms-sel-ctn input, .select2-selection, .selectize-input, .chosen-single, .chosen-choices, input[type='text']"
    ) || container;
    clickTarget.dispatchEvent(new MouseEvent("mousedown", {bubbles: true, cancelable: true, view: window}));
    clickTarget.click();
    clickTarget.dispatchEvent(new MouseEvent("mouseup", {bubbles: true, cancelable: true, view: window}));

    if (window.jQuery) {
        try {
            const candidate = root.matches?.("select, input") ? root : root.querySelector?.("select, input");
            if (candidate && window.jQuery(candidate).data("select2")) {
                window.jQuery(candidate).select2("open");
            }
        } catch (_error) {}
    }

    const input = container.querySelector(
        ".ms-sel-ctn input, .select2-search__field, .selectize-input input, .chosen-search input, input[type='text']"
    ) || document.querySelector(".select2-search__field, .chosen-search input");
    if (input) {
        input.focus();
        input.dispatchEvent(new Event("input", {bubbles: true}));
        input.dispatchEvent(new Event("change", {bubbles: true}));
    }
    return true;
}"""


_MAGIC_CLICK_FIRST_SCRIPT = """(selector) => {
    const root = document.querySelector(selector);
    if (!root) return false;
    const nodes = [root, root.parentElement, root.previousElementSibling, root.nextElementSibling]
        .filter(Boolean);
    const closestGroup = root.closest?.(".form-group, .control-group, .form-row, .row, tr");
    if (closestGroup) nodes.push(closestGroup);
    for (const child of Array.from(root.querySelectorAll("*"))) {
        nodes.push(child);
    }
    if (closestGroup) {
        for (const child of Array.from(closestGroup.querySelectorAll("*"))) {
            nodes.push(child);
        }
    }
    const container = nodes.find(node => (
        node.matches?.(".ms-ctn, .ms-parent, .magic-suggest, .select2-container, .selectize-control, .chosen-container")
        || node.querySelector?.(
            ".ms-sel-ctn input, .ms-trigger, .select2-selection, .selectize-input, .chosen-single, .chosen-choices"
        )
    ));
    const visible = (el) => {
        if (!el) return false;
        const style = window.getComputedStyle(el);
        const rect = el.getBoundingClientRect();
        return style.display !== "none" && style.visibility !== "hidden"
            && rect.width > 0 && rect.height > 0;
    };
    const optionSelector = [
        ".ms-res-item",
        ".ms-res-item-active",
        ".select2-results__option",
        ".selectize-dropdown-content .option",
        ".chosen-results li",
        ".ui-menu-item",
        ".tt-suggestion",
        "[role='option']",
    ].join(", ");
    const localItems = container ? Array.from(container.querySelectorAll(optionSelector)) : [];
    const globalItems = Array.from(document.querySelectorAll(optionSelector));
    const item = localItems.concat(globalItems).find(candidate => (
        visible(candidate) && String(candidate.textContent || "").trim()
        && candidate.getAttribute("aria-disabled") !== "true"
        && !candidate.classList.contains("disabled")
        && !candidate.classList.contains("select2-results__message")
        && !candidate.classList.contains("loading-results")
    ));
    if (!item) return false;
    item.dispatchEvent(new MouseEvent("mousedown", {bubbles: true, cancelable: true, view: window}));
    item.click();
    item.dispatchEvent(new MouseEvent("mouseup", {bubbles: true, cancelable: true, view: window}));
    return true;
}"""


_MAGIC_TYPE_SCRIPT = """([selector, value]) => {
    const root = document.querySelector(selector);
    if (!root) return false;
    root.scrollIntoView({block: "center", inline: "nearest"});
    root.dispatchEvent(new MouseEvent("mousedown", {bubbles: true, cancelable: true, view: window}));
    root.click();
    root.dispatchEvent(new MouseEvent("mouseup", {bubbles: true, cancelable: true, view: window}));

    const input = root.querySelector(".ms-sel-ctn input, input[type='text']");
    if (!input) return false;
    input.focus();
    input.value = value;
    input.dispatchEvent(new Event("input", {bubbles: true}));
    input.dispatchEvent(new Event("change", {bubbles: true}));
    for (const letter of value) {
        input.dispatchEvent(new KeyboardEvent("keydown", {key: letter, bubbles: true}));
        input.dispatchEvent(new KeyboardEvent("keypress", {key: letter, bubbles: true}));
        input.dispatchEvent(new KeyboardEvent("keyup", {key: letter, bubbles: true}));
    }
    return true;
}"""


_QIDIAN_CHAPTER_LINKS_SCRIPT = r"""(limit) => {
    const normalize = (value) => (value || "").replace(/\s+/g, " ").trim();
    const byHref = new Map();
    const chineseNumber = (value) => {
        const digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9};
        const units = {"十": 10, "百": 100, "千": 1000, "万": 10000};
        let total = 0;
        let section = 0;
        let number = 0;
        for (const char of value) {
            if (Object.prototype.hasOwnProperty.call(digits, char)) {
                number = digits[char];
            } else if (Object.prototype.hasOwnProperty.call(units, char)) {
                const unit = units[char];
                if (unit === 10000) {
                    section = (section + number) * unit;
                    total += section;
                    section = 0;
                    number = 0;
                } else {
                    section += (number || 1) * unit;
                    number = 0;
                }
            }
        }
        return total + section + number;
    };
    const isChapterTitle = (text) => /^第\s*([0-9零〇一二两三四五六七八九十百千万]+)\s*章/.test(text) || /^(序章|楔子|引子)/.test(text);
    const chapterNumber = (text) => {
        if (/^(序章|楔子|引子)/.test(text)) return 0;
        const match = text.match(/^第\s*([0-9零〇一二两三四五六七八九十百千万]+)\s*章/);
        if (!match) return Number.MAX_SAFE_INTEGER;
        return /^\d+$/.test(match[1]) ? Number(match[1]) : chineseNumber(match[1]);
    };
    const isServiceTitle = (text) => /(最新章节|已更新至|免费试读)/.test(text);
    for (const anchor of Array.from(document.querySelectorAll("a[href]"))) {
        const href = anchor.href || "";
        const text = normalize(anchor.innerText || anchor.textContent || "");
        if (!/\/chapter\/\d+\/\d+\/?/.test(href)) continue;

        if (!byHref.has(href)) {
            byHref.set(href, {href, title: "", index: byHref.size, number: Number.MAX_SAFE_INTEGER, isChapter: false, isService: false});
        }
        const item = byHref.get(href);
        if (isChapterTitle(text)) {
            item.title = text;
            item.number = chapterNumber(text);
            item.isChapter = true;
            item.isService = false;
        } else if (!item.title && text && !isServiceTitle(text)) {
            item.title = text;
        } else if (isServiceTitle(text) && !item.isChapter) {
            item.isService = true;
        }
    }
    const items = Array.from(byHref.values()).sort((left, right) => left.index - right.index);
    const preferred = items
        .filter(item => item.isChapter)
        .sort((left, right) => (left.number - right.number) || (left.index - right.index));
    const fallback = items.filter(item => !item.isService);
    return (preferred.length ? preferred : fallback)
        .slice(0, limit || 2)
        .map(({href, title}) => ({href, title}));
}"""


_QIDIAN_CHAPTER_TEXT_SCRIPT = r"""() => {
    const text = (node) => node ? (node.innerText || node.textContent || "").replace(/\r/g, "").trim() : "";
    const title =
        text(document.querySelector("h1")) ||
        text(document.querySelector("[class*='title']")) ||
        (document.title || "").split("_")[0].trim();

    const paragraphs = Array.from(document.querySelectorAll("main .content-text, .content-text"))
        .map(node => text(node))
        .filter(Boolean);
    if (paragraphs.length) {
        return {title, text: paragraphs.join("\n")};
    }

    const candidates = [
        "main[id^='c-']",
        "main.content",
        ".chapter-content",
        ".read-content",
        ".chapter-wrapper",
        "article",
    ];
    for (const selector of candidates) {
        const value = text(document.querySelector(selector));
        if (value.length > 200) return {title, text: value};
    }
    return {title, text: ""};
}"""


_FANQIE_EXTRACT_SCRIPT = r"""() => {
    const state = window.__INITIAL_STATE__ || {};
    const page = state.page || {};
    const text = (node) => (node && node.textContent ? node.textContent.replace(/\s+/g, " ").trim() : "");
    const attr = (selector, name) => {
        const node = document.querySelector(selector);
        return node ? (node.getAttribute(name) || "").trim() : "";
    };
    const meta = (name) => (
        attr(`meta[property="${name}"]`, "content") ||
        attr(`meta[name="${name}"]`, "content")
    );
    const firstText = (selectors) => {
        for (const selector of selectors) {
            const value = text(document.querySelector(selector));
            if (value) return value;
        }
        return "";
    };
    const firstAttr = (selectors, name) => {
        for (const selector of selectors) {
            const value = attr(selector, name);
            if (value) return value;
        }
        return "";
    };
    const jsonLdImages = () => {
        for (const script of Array.from(document.querySelectorAll('script[type="application/ld+json"]'))) {
            try {
                const payload = JSON.parse(script.textContent || "{}");
                const image = Array.isArray(payload.image) ? payload.image[0] : payload.image;
                if (image) return image;
                const images = Array.isArray(payload.images) ? payload.images[0] : payload.images;
                if (images) return images;
            } catch (_) {}
        }
        return "";
    };
    return {
        title: page.bookName || firstText(["h1", ".info-name h1"]) || (document.title || "").split("_")[0].replace(/完整版在线免费阅读$/, "").trim(),
        author: page.author || firstText([".author-name-text", ".author-name"]) || "",
        description: page.abstract || firstText([".page-abstract-content p", ".page-abstract-content"]) || meta("description") || "",
        cover_url:
            page.thumbUrl ||
            page.thumbUri ||
            jsonLdImages() ||
            firstAttr([".book-cover-img", ".book-cover img", "img[alt]"], "src"),
        body_text: document.body && document.body.innerText ? document.body.innerText.replace(/\r/g, "").trim() : "",
        meta_description: meta("description")
    };
}"""


_FANQIE_CHAPTER_LINKS_SCRIPT = r"""(limit) => {
    const state = window.__INITIAL_STATE__ || {};
    const page = state.page || {};
    const normalize = (value) => (value || "").replace(/\s+/g, " ").trim();
    const result = [];
    const seen = new Set();
    const add = (href, title, order) => {
        if (!href || seen.has(href)) return;
        seen.add(href);
        result.push({href, title: normalize(title), order: Number(order) || result.length + 1});
    };

    const volumes = Array.isArray(page.chapterListWithVolume) ? page.chapterListWithVolume : [];
    for (const volume of volumes) {
        for (const item of (Array.isArray(volume) ? volume : [])) {
            if (!item || !item.itemId) continue;
            add(new URL(`/reader/${item.itemId}`, location.href).href, item.title || "", item.realChapterOrder);
        }
    }

    if (!result.length) {
        for (const anchor of Array.from(document.querySelectorAll('a[href*="/reader/"]'))) {
            add(anchor.href, anchor.innerText || anchor.textContent || "", result.length + 1);
        }
    }

    return result
        .sort((left, right) => (left.order - right.order))
        .slice(0, limit || 3)
        .map(({href, title}) => ({href, title}));
}"""


_FANQIE_CHAPTER_TEXT_SCRIPT = r"""() => {
    const state = window.__INITIAL_STATE__ || {};
    const data = (state.reader && state.reader.chapterData) || {};
    const text = (node) => node ? (node.innerText || node.textContent || "").replace(/\r/g, "").trim() : "";
    const htmlToText = (value) => {
        const div = document.createElement("div");
        div.innerHTML = value || "";
        return text(div);
    };
    const title =
        data.title ||
        text(document.querySelector(".muye-reader-title")) ||
        text(document.querySelector("h1")) ||
        (document.title || "").split("_")[0].trim();
    const fromState = htmlToText(data.content || "");
    if (fromState) return {title, text: fromState};

    const paragraphs = Array.from(document.querySelectorAll(".muye-reader-content p, .reader-content p, article p"))
        .map(node => text(node))
        .filter(Boolean);
    if (paragraphs.length) {
        return {title, text: paragraphs.join("\n")};
    }

    const candidates = [".muye-reader-content", ".reader-content", "article"];
    for (const selector of candidates) {
        const value = text(document.querySelector(selector));
        if (value.length > 200) return {title, text: value};
    }
    return {title, text: ""};
}"""


_QIDIAN_EXTRACT_SCRIPT = r"""() => {
    const text = (node) => (node && node.textContent ? node.textContent.replace(/\s+/g, " ").trim() : "");
    const multilineText = (node) => (node && node.textContent
        ? node.textContent.replace(/\r/g, "").replace(/[ \t]+\n/g, "\n").replace(/\n{3,}/g, "\n\n").trim()
        : "");
    const attr = (selector, name) => {
        const node = document.querySelector(selector);
        return node ? (node.getAttribute(name) || "").trim() : "";
    };
    const firstText = (selectors) => {
        for (const selector of selectors) {
            const value = text(document.querySelector(selector));
            if (value) return value;
        }
        return "";
    };
    const firstMultilineText = (selectors) => {
        for (const selector of selectors) {
            const value = multilineText(document.querySelector(selector));
            if (value) return value;
        }
        return "";
    };
    const firstAttr = (selectors, name) => {
        for (const selector of selectors) {
            const value = attr(selector, name);
            if (value) return value;
        }
        return "";
    };
    const meta = (name) => (
        attr(`meta[property="${name}"]`, "content") ||
        attr(`meta[name="${name}"]`, "content")
    );
    const bodyText = () => (document.body && document.body.innerText
        ? document.body.innerText.replace(/\r/g, "").trim()
        : "");
    const cleanAuthor = (value) => {
        value = (value || "").replace(/^作者[:：]\s*/, "").replace(/\s+/g, " ").trim();
        value = value.split(/[，,。|]/)[0].trim();
        const badgeTexts = new Set(["白金", "大神", "签约", "VIP", "连载", "完本", "作家"]);
        if (!value || badgeTexts.has(value) || value.length > 32) return "";
        return value;
    };
    const authorFromBody = () => {
        const body = bodyText();
        const patterns = [
            /作者[:：]\s*([^\n\r]+)/,
            /(?:^|\n)\s*([^\n\r]{1,20})\s*\n\s*(?:阅文集团|作品总数|累计字数)/,
            /(?:^|\n)\s*([^\n\r]{1,20})创作的/
        ];
        for (const pattern of patterns) {
            const match = body.match(pattern);
            if (match) {
                const candidate = cleanAuthor(match[1]);
                if (candidate) return candidate;
            }
        }
        return "";
    };
    const descriptionFromBody = () => {
        const body = bodyText();
        const lines = body.replace(/\r/g, "").split("\n").map((line) => line.replace(/[ \t\u00a0]+/g, " ").trim());
        const headers = new Set(["作品简介", "内容简介", "书籍简介", "小说简介", "作品介绍", "内容介绍"]);
        const isHeader = (line) => headers.has(line) || (line.length <= 16 && Array.from(headers).some((header) => line.endsWith(header)));
        const start = lines.findIndex(isHeader);
        if (start < 0) return "";
        const stopLines = new Set(["男生月票榜", "女生月票榜", "月票", "推荐票", "打赏", "本月票数", "本周打赏人数", "包含本书的书单", "目录", "书友互动", "本书荣誉"]);
        const isStopLine = (line) => (
            stopLines.has(line) ||
            line.startsWith("男生月票榜") ||
            line.startsWith("女生月票榜") ||
            line.startsWith("包含本书的书单") ||
            line.startsWith("目录 ")
        );
        const isLikelyTag = (line) => (
            line &&
            line.length <= 8 &&
            /[\u4e00-\u9fff]/.test(line) &&
            !/[。！？!?…，、；;：:《》“”"'（）()]/.test(line)
        );
        let stopReached = false;
        let entries = [];
        for (const line of lines.slice(start + 1)) {
            if (!line) {
                if (entries.length && entries[entries.length - 1] !== null) entries.push(null);
                continue;
            }
            if (isStopLine(line)) {
                stopReached = true;
                break;
            }
            entries.push(line);
        }
        while (entries.length && entries[0] === null) entries.shift();
        while (entries.length && entries[entries.length - 1] === null) entries.pop();
        if (
            stopReached &&
            entries.length >= 2 &&
            entries[entries.length - 2] === null &&
            isLikelyTag(entries[entries.length - 1])
        ) {
            entries = entries.slice(0, -2);
        }
        return entries
            .map((entry) => entry === null ? "" : entry)
            .join("\n")
            .replace(/\n{3,}/g, "\n\n")
            .trim();
    };
    const imageFromSrcset = (srcset) => {
        if (!srcset) return "";
        const first = srcset.split(",")[0] || "";
        return first.trim().split(/\s+/)[0] || "";
    };
    const authorLink = document.querySelector('a[href*="my.qidian.com/author"], a[href*="/author/"]');
    const author =
        cleanAuthor(meta("og:novel:author")) ||
        cleanAuthor(text(authorLink)) ||
        cleanAuthor(firstText([".book-info .writer", ".book-info .author", ".writer", ".author"])) ||
        authorFromBody();
    const cover =
        meta("og:image") ||
        meta("og:novel:book_cover") ||
        firstAttr([
            ".book-img img",
            ".book-cover img",
            ".book-info img",
            "img[src*='bookcover']",
            "img"
        ], "src") ||
        imageFromSrcset(firstAttr([".book-img img", ".book-cover img", "img"], "srcset"));
    const description =
        descriptionFromBody() ||
        firstMultilineText([
            ".book-intro p",
            ".book-intro",
            ".intro p",
            ".intro",
            ".book-info-detail .intro",
            "[class*='intro'] p",
            "[class*='desc']"
        ]) ||
        "";
    const fullBodyText = bodyText();
    return {
        title: firstText(["h1", ".book-info h1", ".book-name", "[class*='bookName']"]) || meta("og:title"),
        author,
        description,
        cover_url: cover,
        body_text: fullBodyText,
        meta_description: meta("description")
    };
}"""
