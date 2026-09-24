"""Сколько сервис просит подождать после 429, по его собственным словам.

Каждый API говорит об этом по-своему: заголовок ``Retry-After`` (секунды или
HTTP-дата), ``retryAfterMs`` в теле у OmniRoute, ``RetryInfo.retryDelay``
(«26s») у Google, метка сброса ``resetTime``, либо просто фраза в сообщении —
«Your quota will reset after 4h32m10s», «try again in 26 seconds». Здесь всё
это сводится к числу секунд; None значит, что сервис ничего не подсказал.
"""

from __future__ import annotations

import json
import math
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

_MS_KEYS = frozenset({"retryafterms", "retry_after_ms"})
_SECONDS_KEYS = frozenset({
    "retry_after_seconds", "retryafterseconds", "retry_after", "retryafter",
    "reset_after_seconds", "reset_in_seconds",
})
_DURATION_KEYS = frozenset({"retrydelay", "retry_delay"})
_RESET_AT_KEYS = frozenset({"resettime", "reset_time", "resets_at", "reset_at", "resetat"})
_MAX_WALK_DEPTH = 6

_UNIT_SECONDS = {
    "ms": 0.001,
    "s": 1.0, "sec": 1.0, "secs": 1.0, "second": 1.0, "seconds": 1.0,
    "m": 60.0, "min": 60.0, "mins": 60.0, "minute": 60.0, "minutes": 60.0,
    "h": 3600.0, "hr": 3600.0, "hrs": 3600.0, "hour": 3600.0, "hours": 3600.0,
}
# Длинные единицы раньше коротких, иначе «minutes» разберётся как «m» + «inutes».
# После единицы не может идти буква: «5 months» — не «5 m».
_UNIT_TOKEN = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(ms|hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)(?![a-zа-я])",
    re.IGNORECASE,
)
_TEXT_ANCHOR = re.compile(
    r"(?:resets?|retry(?:\s+again)?|try\s+again|wait|сброс|повтор(?:ите)?)\s*"
    r"(?:after|in|через|:)?\s*(?:about|approximately|~)?\s*",
    re.IGNORECASE,
)
_TOKEN_GLUE = re.compile(r"^\s*(?:,|and|и)?\s*", re.IGNORECASE)


def retry_after_seconds(headers=None, body_text=None, *, now=None):
    """Секунды до следующей попытки по подсказке сервиса, либо None без подсказки.

    Порядок доверия: заголовок ``Retry-After``, затем поля тела (миллисекунды,
    секунды, длительности вроде «26s», метка сброса), затем фраза в сообщении.
    Отрицательные значения (сброс уже позади) отдаются как 0.0.
    """
    moment = time.time() if now is None else float(now)

    seconds = _from_header(headers, moment)
    if seconds is not None:
        return seconds

    text = str(body_text or "")
    payload = _parse_json(text)
    if payload is not None:
        seconds = _from_payload(payload, moment)
        if seconds is not None:
            return seconds

    return _from_text(text)


def _from_header(headers, moment):
    value = _header_value(headers, "retry-after")
    if value is None:
        return None
    return _seconds_from_scalar(value, moment)


def _header_value(headers, name):
    if headers is None:
        return None
    getter = getattr(headers, "get", None)
    if callable(getter):
        for candidate in (name, name.title(), "Retry-After"):
            value = getter(candidate)
            if value is not None:
                return value
    try:
        items = headers.items()
    except AttributeError:
        return None
    for key, value in items:
        if str(key).lower() == name:
            return value
    return None


def _parse_json(text):
    stripped = text.strip()
    if not stripped or stripped[0] not in "{[":
        return None
    try:
        return json.loads(stripped)
    except ValueError:
        return None


def _from_payload(payload, moment):
    found = {"ms": None, "seconds": None, "duration": None, "reset_at": None}
    _walk(payload, found, moment, depth=0)
    for category in ("ms", "seconds", "duration", "reset_at"):
        if found[category] is not None:
            return found[category]
    return None


def _walk(node, found, moment, depth):
    if depth > _MAX_WALK_DEPTH:
        return
    if isinstance(node, dict):
        for raw_key, value in node.items():
            key = str(raw_key).strip().lower().replace("-", "_")
            if key in _MS_KEYS and found["ms"] is None:
                number = _positive_number(value)
                if number is not None:
                    found["ms"] = max(0.0, number / 1000.0)
            elif key in _SECONDS_KEYS and found["seconds"] is None:
                found["seconds"] = _seconds_from_scalar(value, moment)
            elif key in _DURATION_KEYS and found["duration"] is None:
                found["duration"] = _duration_seconds(str(value)) if value is not None else None
            elif key in _RESET_AT_KEYS and found["reset_at"] is None:
                found["reset_at"] = _seconds_until(value, moment)
            elif isinstance(value, (dict, list)):
                _walk(value, found, moment, depth + 1)
    elif isinstance(node, list):
        for value in node:
            _walk(value, found, moment, depth + 1)


def _positive_number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _seconds_from_scalar(value, moment):
    """Число секунд, длительность («26s», «4h32m»), HTTP-дата или ISO-метка."""
    if value is None or isinstance(value, bool):
        return None
    number = _positive_number(value)
    if number is not None and not isinstance(value, str):
        return max(0.0, number)
    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        return max(0.0, float(text))
    duration = _duration_seconds(text)
    if duration is not None:
        return duration
    return _seconds_until(text, moment)


def _duration_seconds(text):
    """«26s», «1.5s», «500ms», «4h32m10s», «2 minutes» → секунды; иначе None."""
    total = 0.0
    matched = False
    position = 0
    stripped = text.strip()
    while position < len(stripped):
        glue = _TOKEN_GLUE.match(stripped, position)
        if glue:
            position = glue.end()
        token = _UNIT_TOKEN.match(stripped, position)
        if not token:
            break
        total += float(token.group(1).replace(",", ".")) * _UNIT_SECONDS[token.group(2).lower()]
        matched = True
        position = token.end()
    if not matched or stripped[position:].strip():
        return None
    return total


def _seconds_until(value, moment):
    """Секунды до метки времени: HTTP-дата, ISO 8601 или epoch (с/мс)."""
    if value is None or isinstance(value, bool):
        return None
    number = _positive_number(value)
    if number is not None and not isinstance(value, str):
        stamp = number / 1000.0 if number > 1e12 else number
        return max(0.0, stamp - moment)
    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r"\d{10,13}(?:\.\d+)?", text):
        stamp = float(text)
        stamp = stamp / 1000.0 if stamp > 1e12 else stamp
        return max(0.0, stamp - moment)
    parsed = None
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        parsed = None
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0.0, parsed.timestamp() - moment)


def _from_text(text):
    """Длительность после «reset after», «retry in», «try again in», «wait»."""
    for anchor in _TEXT_ANCHOR.finditer(text):
        position = anchor.end()
        total = 0.0
        matched = False
        while position < len(text):
            glue = _TOKEN_GLUE.match(text, position)
            if glue:
                position = glue.end()
            token = _UNIT_TOKEN.match(text, position)
            if not token:
                break
            total += float(token.group(1).replace(",", ".")) * _UNIT_SECONDS[token.group(2).lower()]
            matched = True
            position = token.end()
        if matched:
            return total
    return None
