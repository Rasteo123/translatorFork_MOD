# -*- coding: utf-8 -*-
"""Системные окна LitRPG-новелл: поиск серий системных строк в переведённой главе.

Модуль без Qt. Работает над сырым HTML главы: абзацы находит через
BeautifulSoup(html.parser), а по ``sourceline``/``sourcepos`` возвращается к
точным смещениям в исходной строке. Замена потом трогает только сами абзацы,
остальной файл остаётся байт в байт прежним.
"""

from __future__ import annotations

import html as html_module
import os
import re
from dataclasses import dataclass
from pathlib import Path

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from .io_utils import atomic_write_text
from .translation_versions import select_target_translation_version

BLOCK_ATTR = "data-sys"

_BRACKET_PAIRS = {"[": "]", "【": "】", "〖": "〗"}
_DECOR_CHARS = "◆◇★☆✦✧▲△■□●○※◈❖"

DEFAULT_TRIGGERS = (
    "статус", "панель", "характеристик", "навык", "способност", "умени",
    "уровень", "достижени", "титул", "квест", "задание", "предмет",
    "система", "уведомлени", "награда", "очки", "ранг", "талант",
    "hp", "mp", "sp", "exp", "опыт",
)

_KIND_RULES = (
    ("levelup", re.compile(r"уровень повышен|повышение уровня|level ?up", re.I)),
    ("achievement", re.compile(r"достижени|титул", re.I)),
    ("status", re.compile(r"статус|панель|характеристик|status", re.I)),
    ("skill", re.compile(r"навык|способност|умени[ея]|заклинани|skill", re.I)),
)
DEFAULT_KIND = "notice"

_KV_RE = re.compile(r"^[^:：]{1,40}[:：]\s*\S")
_TRAILING_PUNCT = ".!?…"
_MAX_SPAN_PARAGRAPHS = 20
# Авторские и переводческие примечания в скобках и служебные пометки
# вроде «[Конец главы]» — не системные окна.
DEFAULT_EXCLUDE = (
    r"прим(\.|ечани\w*)\s*(автора|авт\.|пер\.|переводчика)|^[\[【(]?\s*P\.?\s?S\.?\b"
    r"|^[\[【]?\s*(конец главы|продолжение следует)|благодар\w*\s+за\s+(донат|пожертв|поддержк|лунн|подар)"
)
_CLOSING_P_RE = re.compile(r"</p\s*>", re.I)
_OPENING_P_RE = re.compile(r"<p[\s>/]", re.I)


@dataclass(frozen=True)
class DetectorSettings:
    triggers: tuple[str, ...] = DEFAULT_TRIGGERS
    single_bracketed: bool = True
    exclude_pattern: str = DEFAULT_EXCLUDE


@dataclass
class WindowCandidate:
    start: int
    end: int
    kind: str
    lines: list[str]
    paragraph_html: list[str]


@dataclass
class _Paragraph:
    start: int
    end: int
    text: str
    adjacent: bool


# --- разбор абзацев ---------------------------------------------------------

def _line_offsets(raw: str) -> list[int]:
    offsets = [0]
    for line in raw.split("\n"):
        offsets.append(offsets[-1] + len(line) + 1)
    return offsets


def _next_element(tag: Tag):
    node = tag.next_sibling
    while node is not None:
        if isinstance(node, Comment) or (isinstance(node, NavigableString) and not str(node).strip()):
            node = node.next_sibling
            continue
        return node
    return None


def _paragraph_end(raw: str, start: int) -> int | None:
    closing = _CLOSING_P_RE.search(raw, start + 2)
    if closing is None:
        return None
    if _OPENING_P_RE.search(raw, start + 2, closing.start()):
        return None
    return closing.end()


def _paragraphs(raw: str) -> list[_Paragraph]:
    soup = BeautifulSoup(raw, "html.parser")
    offsets = _line_offsets(raw)
    result: list[_Paragraph] = []
    previous_tag = None
    for tag in soup.find_all("p"):
        if tag.sourceline is None or tag.find_parent(attrs={BLOCK_ATTR: True}) is not None:
            continue
        start = offsets[tag.sourceline - 1] + tag.sourcepos
        end = _paragraph_end(raw, start)
        if end is None:
            previous_tag = None
            continue
        text = " ".join(tag.get_text(" ", strip=True).split())
        adjacent = previous_tag is not None and _next_element(previous_tag) is tag
        result.append(_Paragraph(start=start, end=end, text=text, adjacent=adjacent))
        previous_tag = tag
    return result


# --- классификация строк ----------------------------------------------------

_FULL_RE = re.compile(r"^([\[【〖])(.*)([\]】〗])([.!?…]*)$", re.S)
_KEYED_RE = re.compile(r"^[\[【〖]([^\]】〗]{1,60})[\]】〗](.*)$", re.S)
_GROUP_LIST_RE = re.compile(r"^(\s*[,;]?\s*[\[【〖][^\[\]【】〖〗]{1,60}[\]】〗])*\s*[.!?…]*$")
_ATTRIBUTION_RE = re.compile(
    r"^[–—-]\s*(?:\w+\s+){0,3}(?:сказал|ответил|добавил|произн|спросил|воскликн|отозвал|проговор|"
    r"пробормот|заяв|подтверд|уточн|польстил|взвил|отрезал|напомнил|голос)",
    re.I,
)
# Системное сообщение в «кавычках» и репликой «— [...]»: узнаётся по началу.
_QUOTED_RE = re.compile(r"^«([^«»]{12,})»([.!?…]*)$")
_DASHED_RE = re.compile(r"^[—–-]\s*([\[【]\S.*[\]】])([.!?…]*)$", re.S)
_SYSTEM_START_RE = re.compile(
    r"^(динь|дзынь|дин-дон|поздравля|вниман|обнаружен|система[:：]|уведомлени|вы получ|"
    r"задание[:：]|новое задание|награда[:：]|награда за|уровень повыш|активирован|оповещени|"
    r"предупреждени|подтвердите|желаете)",
    re.I,
)
_VOCATIVE_RE = re.compile(r"^система\s*[,?!…]", re.I)
_SOUND_WORDS = frozenset("динь дзынь дин дон лянь клац бам бум дзинь тук".split())


def _has_real_words(inner: str) -> bool:
    """Не одно звукоподражание вроде «Динь-дон! Динь-дон!»."""
    words = [word.lower() for word in re.findall(r"[^\W\d_]+", inner)]
    return sum(1 for word in words if word not in _SOUND_WORDS) >= 3


def _outer_group_spans_all(text: str) -> bool:
    """Первая скобка закрывается только в самом конце (вложенные группы допустимы)."""
    opening = text[0]
    closing = _BRACKET_PAIRS.get(opening)
    body = text.rstrip(_TRAILING_PUNCT)
    if closing is None or not body.endswith(closing):
        return False
    depth = 0
    for index, char in enumerate(body):
        if char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0 and index != len(body) - 1:
                return False
            if depth < 0:
                return False
    return depth == 0


def bracket_shape(text: str):
    """Форма строки со скобками.

    ``full`` — строка целиком в скобках (точка снаружи допускается),
    ``keyed`` — ``[Термин] – описание`` или ``[Термин]: значение``,
    ``list`` — перечень групп ``[А], [Б], [В]``,
    ``open`` — скобка открыта и не закрыта (окно тянется по абзацам),
    ``close`` — закрывающая скобка без открывающей, ``None`` — обычный текст.
    """
    if len(text) < 2:
        return None
    opening = text[0]
    if opening == "«":
        quoted = _QUOTED_RE.match(text)
        if quoted:
            inner = quoted.group(1)
            if _SYSTEM_START_RE.match(inner) and _VOCATIVE_RE.match(inner) is None and _has_real_words(inner):
                return "quoted"
        return None
    if opening in "—–-":
        dashed = _DASHED_RE.match(text)
        if dashed and _outer_group_spans_all(dashed.group(1)):
            inner = dashed.group(1)[1:-1].strip()
            if _SYSTEM_START_RE.match(inner) or "систем" in inner.lower():
                return "dashed"
            exclamatory = inner.endswith("!") or "!" in dashed.group(2)
            data_like = ":" in inner or any(char.isdigit() for char in inner) or len(inner) >= 40
            if len(inner) >= 20 and not exclamatory and data_like:
                return "dashed"
        return None
    if opening in _BRACKET_PAIRS:
        closing = _BRACKET_PAIRS[opening]
        if _outer_group_spans_all(text):
            return "full"
        keyed = _KEYED_RE.match(text)
        if keyed:
            group, rest = keyed.group(1), keyed.group(2)
            if _GROUP_LIST_RE.match(rest) and rest.strip(" .!?…,;"):
                return "list"
            rest = rest.lstrip()
            group_is_term = not any(char in group for char in "!?") or not any(char.isalpha() for char in group)
            separator_ok = not rest.startswith((",", ";")) or (rest.startswith(";") and ":" in rest)
            if rest and group_is_term and separator_ok and _ATTRIBUTION_RE.match(rest) is None:
                return "keyed"
            return None
        if closing not in text:
            return "open"
        return None
    stripped = text.rstrip(_TRAILING_PUNCT)
    for pair_opening, pair_closing in _BRACKET_PAIRS.items():
        if stripped.endswith(pair_closing) and pair_opening not in text:
            return "close"
    return None


def is_bracketed(text: str) -> bool:
    return bracket_shape(text) == "full"


def strip_brackets(text: str) -> str:
    """Текст для показа: без скобок вокруг, знак после скобки остаётся внутри."""
    shape = bracket_shape(text)
    if shape == "full":
        match = _FULL_RE.match(text)
        return match.group(2).strip() + match.group(4)
    if shape == "quoted":
        match = _QUOTED_RE.match(text)
        return match.group(1).strip() + match.group(2)
    if shape == "dashed":
        match = _DASHED_RE.match(text)
        return match.group(1)[1:-1].strip() + match.group(2)
    if shape == "list":
        return " ".join(re.sub(r"[\[\]【】〖〗]", "", text).split())
    if shape == "open":
        return text[1:].strip()
    if shape == "close":
        return re.sub(r"[\]】〗]([.!?…]*)$", r"\1", text).strip()
    return text


def _is_decorated(text: str) -> bool:
    return len(text) >= 3 and text[0] in _DECOR_CHARS and text[-1] in _DECOR_CHARS


def _is_caps(text: str) -> bool:
    letters = [char for char in text if char.isalpha()]
    return len(letters) >= 4 and all(char.isupper() for char in letters)


def _is_dialogue(text: str) -> bool:
    return text.startswith(("—", "–", "- "))


_QUOTE_CHARS = "«\"“„'"
_VALUE_BAD_START = _QUOTE_CHARS + "—–-"
_SENTENCE_END = (".", "!", "?", "…", ":", ";", ",")


def _mostly_alphanumeric(value: str) -> bool:
    """Значение из букв и цифр, а не смайлик вроде ``(º Д º*)``."""
    compact = value.replace(" ", "")
    alphanumeric = sum(1 for char in compact if char.isalnum())
    return alphanumeric > 0 and alphanumeric * 2 >= len(compact)


_STAT_KEYS = frozenset(
    "имя раса титул уровень здоровье мана сила ловкость телосложение интеллект мудрость харизма "
    "выносливость скорость магия класс опыт награда ранг навык навыки способность способности талант "
    "статус очки мировоззрение возраст пол прочность защита атака урон звание профессия оружие броня "
    "снаряжение питомец задание квест условие эффект описание тип стоимость длительность hp mp sp exp".split()
)
_QUOTED_KEY_RE = re.compile(r"^«[^»]{1,40}»\s*[:：]")
_KEY_QUOTES = "«»\"“”"
# Шапка книги и заголовки глав: «Автор: …», «Глава 9: Нина.» — не карточка статуса.
_META_KEYS = frozenset(
    "автор название переводчик перевод источник издательство аннотация оригинал жанр "
    "глава часть том пролог эпилог".split()
)


def _is_stat_key(key: str) -> bool:
    words = key.lower().split()
    return bool(words) and (key.lower() in _STAT_KEYS or words[0] in _STAT_KEYS)


def is_key_value(text: str) -> bool:
    """Строка вида ``Ключ: значение`` (или несколько таких через ``|``).

    Чат-реплики ``«Ник: текст»`` и сценарные реплики ``Имя: «…»`` / ``Имя: — …``
    сюда не попадают: они начинаются с кавычки или их значение начинается с
    кавычки либо тире. Точка в конце допустима для карточек статуса:
    ключ из известных характеристик или значение с заглавной буквы либо цифры.
    """
    inner = strip_brackets(text)
    if not inner or _is_dialogue(inner) or len(inner) > 200 or inner[0] == "(":
        return False
    if inner[0] in _QUOTE_CHARS and _QUOTED_KEY_RE.match(inner) is None:
        return False
    stripped = inner.rstrip()
    if stripped.endswith(("!", "?", "…", ":")):
        return False
    ends_with_period = stripped.endswith(".")
    unbracketed = bracket_shape(text) is None
    parts = [part.strip() for part in inner.split("|")] if "|" in inner else [inner]
    for part in parts:
        if _KV_RE.match(part) is None:
            continue
        key, value = re.split(r"[:：]", part, maxsplit=1)
        key = key.strip().strip(_KEY_QUOTES)
        value = value.strip()
        if not value or len(key.split()) > 4 or value[0] in _VALUE_BAD_START or len(value) > 120:
            continue
        if unbracketed and key.lower().split()[0] in _META_KEYS:
            continue
        if not _mostly_alphanumeric(value):
            continue
        if ends_with_period and not _is_stat_key(key) and not (value[0].isupper() or value[0].isdigit()):
            continue
        return True
    return False


def _has_trigger(text: str, triggers) -> bool:
    lowered = text.lower()
    return any(trigger and trigger.lower() in lowered for trigger in triggers)


def _is_header(text: str, settings: DetectorSettings) -> bool:
    if bracket_shape(text) in ("full", "quoted", "dashed"):
        return True
    if len(text) > 80 or _is_dialogue(text) or text[0] in _QUOTE_CHARS:
        return False
    if re.search(r"[:：]\s*[«\"“]", text):
        return False
    if _is_decorated(text) or _is_caps(text):
        return True
    if text.rstrip().endswith(_SENTENCE_END):
        return False
    return _has_trigger(text, settings.triggers)


_DATA_SHAPES = ("full", "keyed", "list", "quoted", "dashed")
_SINGLE_SHAPES = ("full", "quoted", "dashed")


def _is_data_line(text: str) -> bool:
    return bracket_shape(text) in _DATA_SHAPES or is_key_value(text) or _is_decorated(text)


def classify_kind(lines: list[str]) -> str:
    """Тип рамки: повышение уровня, затем карточка с двумя и более парами
    ``Ключ: значение`` (статус даже при слове «титул» внутри), затем по словам."""
    texts = [strip_brackets(line) for line in lines]
    joined = " ".join(texts)
    key_value_count = sum(1 for text in texts if is_key_value(text))
    rules = dict(_KIND_RULES)
    if rules["levelup"].search(joined):
        return "levelup"
    if key_value_count >= 2:
        return "status"
    for kind, pattern in _KIND_RULES[1:]:
        if pattern.search(joined):
            return kind
    return DEFAULT_KIND


# --- поиск серий ------------------------------------------------------------

def _span_end(paragraphs, start: int, excluded) -> int | None:
    """Индекс абзаца, закрывающего скобку, открытую в ``start``; None, если его нет."""
    for index in range(start + 1, min(start + _MAX_SPAN_PARAGRAPHS + 1, len(paragraphs))):
        paragraph = paragraphs[index]
        if not paragraph.adjacent or excluded(paragraph):
            return None
        shape = bracket_shape(paragraph.text)
        if shape == "open":
            return None
        if shape == "close":
            return index
    return None


def find_windows(html: str, settings: DetectorSettings | None = None) -> list[WindowCandidate]:
    """Найти серии системных строк в HTML главы, в порядке документа."""
    settings = settings or DetectorSettings()
    exclude = re.compile(settings.exclude_pattern, re.I) if settings.exclude_pattern else None
    paragraphs = _paragraphs(html)

    def excluded(paragraph: _Paragraph) -> bool:
        return not paragraph.text or (exclude is not None and exclude.search(paragraph.text) is not None)

    windows: list[WindowCandidate] = []
    index = 0
    while index < len(paragraphs):
        first = paragraphs[index]
        text = first.text
        if excluded(first):
            index += 1
            continue

        shape = bracket_shape(text)
        stop = index + 1
        if shape == "open":
            span_end = _span_end(paragraphs, index, excluded)
            if span_end is None:
                index += 1
                continue
            stop = span_end + 1
        elif not (shape in _DATA_SHAPES or _is_header(text, settings) or is_key_value(text)):
            index += 1
            continue

        while (
            stop < len(paragraphs)
            and paragraphs[stop].adjacent
            and not excluded(paragraphs[stop])
            and _is_data_line(paragraphs[stop].text)
        ):
            stop += 1

        length = stop - index
        accepted = length >= 2 or (shape in _SINGLE_SHAPES and settings.single_bracketed)
        if not accepted:
            index += 1
            continue

        chosen = paragraphs[index:stop]
        lines = [paragraph.text for paragraph in chosen]
        windows.append(
            WindowCandidate(
                start=chosen[0].start,
                end=chosen[-1].end,
                kind=classify_kind(lines),
                lines=lines,
                paragraph_html=[html[paragraph.start:paragraph.end] for paragraph in chosen],
            )
        )
        index = stop
    return windows


# --- оформление -------------------------------------------------------------

DEFAULT_TEMPLATES = {
    "status": {
        "label": "Статус", "border": "#4fc3f7", "background": "#0b1622", "text": "#e6edf5",
        "accent": "#7fd3ff", "icon": "◆", "upper": True, "columns": 3,
    },
    "levelup": {
        "label": "Уровень", "border": "#4fc3f7", "background": "#0b1622", "text": "#e6edf5",
        "accent": "#7fd3ff", "icon": "▲", "upper": True, "columns": 1,
    },
    "skill": {
        "label": "Навык", "border": "#b388ff", "background": "#12081f", "text": "#efe6ff",
        "accent": "#d1b3ff", "icon": "◆", "upper": False, "columns": 1,
    },
    "notice": {
        "label": "Уведомление", "border": "#b388ff", "background": "#12081f", "text": "#efe6ff",
        "accent": "#d1b3ff", "icon": "✦", "upper": False, "columns": 1,
    },
    "achievement": {
        "label": "Достижение", "border": "#ffd740", "background": "#1a1206", "text": "#fff3cc",
        "accent": "#ffd740", "icon": "★", "upper": True, "columns": 1,
    },
}
KIND_ORDER = ("status", "skill", "notice", "levelup", "achievement")

_TITLE_KEYS = (
    "навык", "способност", "умени", "заклинани", "достижени", "статус",
    "уведомлени", "система", "квест", "задание", "предмет",
)
_NBSP = "\u00a0"
_SEPARATOR = f"{_NBSP}|{_NBSP}"
_SHORT_VALUE = 25
_ITALIC_FROM = 40


def _escape(text: str) -> str:
    return html_module.escape(text, quote=False)


_TITLE_MAX_CHARS = 60


def _looks_like_title(text: str) -> bool:
    if len(text) > _TITLE_MAX_CHARS or bracket_shape(text) in ("keyed", "list"):
        return False
    if not is_key_value(text):
        return True
    key = re.split(r"[:：]", text, maxsplit=1)[0].lower()
    return any(word in key for word in _TITLE_KEYS)


def _render_key_value_part(part: str, accent: str) -> str:
    key, value = re.split(r"[:：]", part, maxsplit=1)
    key = key.strip().strip(_KEY_QUOTES)
    return f'<b style="color:{accent};">{_escape(key)}:</b> {_escape(value.strip())}'


def _render_keyed(text: str, accent: str) -> str:
    match = _KEYED_RE.match(text)
    term, rest = match.group(1).strip(), match.group(2).strip()
    if rest.startswith((":", "：")):
        return f'<b style="color:{accent};">{_escape(term)}:</b> {_escape(rest[1:].strip())}'
    return f'<b style="color:{accent};">{_escape(term)}</b> {_escape(rest)}'


def _render_row(text: str, accent: str, italic_allowed: bool):
    """Вернуть (html строки, короткая ли это пара ключ-значение для колонок)."""
    if bracket_shape(text) == "keyed":
        return _render_keyed(text, accent), False
    text = strip_brackets(text)
    if is_key_value(text):
        parts = [part.strip() for part in text.split("|")] if "|" in text else [text]
        rendered = []
        for part in parts:
            if _KV_RE.match(part):
                rendered.append(_render_key_value_part(part, accent))
            else:
                rendered.append(_escape(part))
        short = len(parts) == 1 and len(re.split(r"[:：]", text, maxsplit=1)[1].strip()) <= _SHORT_VALUE
        return _SEPARATOR.join(rendered), short
    escaped = _escape(text)
    if italic_allowed and len(text) >= _ITALIC_FROM:
        return f"<i>{escaped}</i>", False
    return escaped, False


def _group_columns(rows, columns: int) -> list[str]:
    grouped: list[str] = []
    pending: list[str] = []

    def flush():
        while pending:
            grouped.append(_SEPARATOR.join(pending[:columns]))
            del pending[:columns]

    for rendered, short in rows:
        if columns > 1 and short:
            pending.append(rendered)
            continue
        flush()
        grouped.append(rendered)
    flush()
    return grouped


def render_window(lines, kind: str, *, templates=None, source_html=None) -> str:
    """Собрать одну строку HTML с рамкой для серии системных строк.

    Одна физическая строка обязательна: загрузчик Rulate оборачивает каждую
    строку md-файла в ``<p>`` и разваливает многострочный блок.
    """
    templates = templates or DEFAULT_TEMPLATES
    template = dict(DEFAULT_TEMPLATES.get(kind, DEFAULT_TEMPLATES[DEFAULT_KIND]))
    template.update(templates.get(kind, {}))
    accent = template["accent"]

    texts = [" ".join(str(line).split()) for line in lines]
    texts = [text for text in texts if text]
    if texts and bracket_shape(texts[0]) not in ("keyed", "list"):
        texts[0] = strip_brackets(texts[0])
    title = None
    if len(texts) >= 2 and _looks_like_title(texts[0]):
        title, texts = texts[0].rstrip(":：").strip(), texts[1:]

    rows = [_render_row(text, accent, italic_allowed=title is not None) for text in texts]
    body_rows = _group_columns(rows, int(template.get("columns") or 1))

    pieces = []
    if title is not None:
        shown = title.upper() if template.get("upper") else title
        icon = str(template.get("icon") or "").strip()
        decorated = f"{icon} {_escape(shown)} {icon}".strip()
        pieces.append(f'<b style="color:{accent};letter-spacing:2px;">{decorated}</b>')
    pieces.extend(body_rows)

    style = (
        f"margin:16px 0;padding:14px 18px;border:2px solid {template['border']};"
        f"border-left:8px solid {template['border']};background:{template['background']};"
        f"color:{template['text']};text-align:center;line-height:1.6;"
    )
    original_attr = ""
    if source_html:
        if not isinstance(source_html, str):
            source_html = "".join(source_html)
        # Переносы строк исходника прячем в сущности: блок обязан остаться одной
        # строкой, а html.unescape вернёт их при снятии оформления.
        original = html_module.escape(source_html, quote=True).replace("\r", "&#13;").replace("\n", "&#10;")
        original_attr = f' {BLOCK_ATTR}-orig="{original}"'
    block = f'<div {BLOCK_ATTR}="{kind}"{original_attr} style="{style}">' + "<br />".join(pieces) + "</div>"
    return re.sub(r"\s*\n\s*", " ", block)


# --- применение и снятие ----------------------------------------------------

_BLOCK_RE = re.compile(r'<div\b[^>]*\bdata-sys="[^"]*"[^>]*>.*?</div>', re.S)
_ORIG_RE = re.compile(r'\bdata-sys-orig="([^"]*)"')
_BR_RE = re.compile(r"<br\s*/?>", re.I)
_TAG_RE = re.compile(r"<[^>]+>")


def _candidate_matches(html: str, candidate: WindowCandidate) -> bool:
    span = html[candidate.start:candidate.end]
    pieces = candidate.paragraph_html
    if not pieces or not span.startswith(pieces[0]) or not span.endswith(pieces[-1]):
        return False
    position = 0
    for piece in pieces:
        position = span.find(piece, position)
        if position < 0:
            return False
        position += len(piece)
    return True


def apply_windows(html: str, candidates, *, templates=None) -> tuple[str, int]:
    """Заменить выбранные серии абзацев блоками с рамкой.

    Кандидаты должны быть получены из :func:`find_windows` на этом же HTML;
    если текст под кандидатом с тех пор изменился, он пропускается.
    Возвращает новый HTML и число обёрнутых серий.
    """
    count = 0
    result = html
    for candidate in sorted(candidates, key=lambda item: item.start, reverse=True):
        if not _candidate_matches(html, candidate):
            continue
        block = render_window(
            candidate.lines,
            candidate.kind,
            templates=templates,
            source_html=html[candidate.start:candidate.end],
        )
        result = result[:candidate.start] + block + result[candidate.end:]
        count += 1
    return result, count


def _fallback_paragraphs(block: str) -> str:
    inner = block[block.index(">") + 1:]
    inner = inner[: inner.rfind("</div>")]
    lines = [" ".join(_TAG_RE.sub("", part).split()) for part in _BR_RE.split(inner)]
    return "\n\n".join(f"<p>{line}</p>" for line in lines if line)


def strip_windows(html: str) -> tuple[str, int]:
    """Снять оформление: вернуть исходные абзацы из ``data-sys-orig``.

    Блок без сохранённого оригинала разбирается на абзацы по ``<br />``.
    """
    count = 0

    def restore(match):
        nonlocal count
        block = match.group(0)
        original = _ORIG_RE.search(block)
        count += 1
        if original is None:
            return _fallback_paragraphs(block)
        return html_module.unescape(original.group(1))

    return _BLOCK_RE.sub(restore, html), count


# --- файлы и проект ---------------------------------------------------------

def process_chapter_file(path, *, mode: str, settings=None, templates=None, candidates=None) -> int:
    """Оформить (``apply``) или снять оформление (``strip``) в файле главы.

    Файл читается и пишется байт в байт без трансляции переводов строк;
    при ``apply`` без ``candidates`` серии ищутся заново. Возвращает число
    затронутых серий; файл переписывается только когда оно больше нуля.
    """
    target = Path(path)
    html = target.read_bytes().decode("utf-8")
    if mode == "apply":
        chosen = list(candidates) if candidates is not None else find_windows(html, settings)
        result, count = apply_windows(html, chosen, templates=templates)
    elif mode == "strip":
        result, count = strip_windows(html)
    else:
        raise ValueError(f"Неизвестный режим: {mode!r}")
    if count:
        atomic_write_text(target, result)
    return count


def project_chapter_files(project_folder) -> list[tuple[str, str]]:
    """Главы проекта в порядке книги: (исходный путь в EPUB, файл рабочей версии).

    Берётся та версия, которую использует сборка EPUB: проверенная, если она
    есть, иначе самая свежая. Главы без файла на диске пропускаются.
    """
    from .project_manager import TranslationProjectManager

    folder = str(project_folder)
    manager = TranslationProjectManager(folder)
    result = []
    for original in manager.get_all_originals():
        versions = manager.get_versions_for_original(original)
        rel_path, _ = select_target_translation_version(versions, folder)
        if not rel_path:
            continue
        full_path = os.path.join(folder, str(rel_path).replace("/", os.sep))
        if not os.path.exists(full_path):
            continue
        result.append((original, full_path))
    return result


# --- проект целиком ---------------------------------------------------------

@dataclass
class ChapterScan:
    original: str
    path: str
    title: str
    candidates: list[WindowCandidate]


def _chapter_title(html: str, original: str) -> str:
    from .epub_tools import extract_first_epub_heading_text

    try:
        title = extract_first_epub_heading_text(html)
    except Exception:
        title = ""
    return title or os.path.basename(original)


def scan_project(project_folder, settings: DetectorSettings | None = None, progress=None) -> list[ChapterScan]:
    """Найти системные окна во всех главах проекта (и главы без окон тоже)."""
    chapters = project_chapter_files(project_folder)
    result = []
    for index, (original, path) in enumerate(chapters, start=1):
        html = Path(path).read_bytes().decode("utf-8")
        result.append(ChapterScan(
            original=original,
            path=path,
            title=_chapter_title(html, original),
            candidates=find_windows(html, settings),
        ))
        if progress is not None:
            progress(index, len(chapters), original)
    return result


def apply_project(selections, templates=None, progress=None) -> tuple[int, int]:
    """Оформить выбранные серии: ``selections`` = [(путь файла, [кандидаты])].

    Возвращает (главы с изменениями, обёрнутые окна).
    """
    chapters = 0
    windows = 0
    selections = list(selections)
    for index, (path, candidates) in enumerate(selections, start=1):
        count = process_chapter_file(path, mode="apply", templates=templates, candidates=candidates)
        if count:
            chapters += 1
            windows += count
        if progress is not None:
            progress(index, len(selections), path)
    return chapters, windows


def strip_project(project_folder, progress=None) -> tuple[int, int]:
    """Снять оформление во всех главах проекта. Возвращает (главы, окна)."""
    chapters = 0
    windows = 0
    files = project_chapter_files(project_folder)
    for index, (original, path) in enumerate(files, start=1):
        count = process_chapter_file(path, mode="strip")
        if count:
            chapters += 1
            windows += count
        if progress is not None:
            progress(index, len(files), original)
    return chapters, windows


# --- образцы и предпросмотр -------------------------------------------------

SAMPLE_WINDOWS = {
    "status": [
        "[Статус персонажа]", "[Имя: Ёдыре]", "[Раса: Человек]", "[Уровень: 14]",
        "[Очки здоровья: 100/100]", "[Очки маны: 0/0]", "[Очки характеристик: 140+28]",
    ],
    "skill": [
        "[Навык: «Благословение Рюнара»]", "[Тип: Пассивный]", "[SP: 8]",
        "[Эксклюзивный навык иномирца. Опыт растёт пропорционально усилиям, дарует иммунитет к проклятиям.]",
    ],
    "notice": ["[Навык активирован]", "[Условия выполнены, опыт повышается!]"],
    "levelup": ["[Повышение уровня]", "[Уровень повышен +2]"],
    "achievement": ["[Достижение: новый титул]", "[Титул: «Ты что, садист??»]"],
}


def render_preview_document(templates=None, extra=None) -> str:
    """Самостоятельная HTML-страница с рамками для точного просмотра в браузере.

    ``extra`` — список ``(строки, тип)``, показывается перед образцами всех
    типов. Блоки те же, что уйдут в главы, но без ``data-sys-orig``.
    """
    blocks = [render_window(lines, kind, templates=templates) for lines, kind in (extra or [])]
    blocks.extend(render_window(lines, kind, templates=templates) for kind, lines in SAMPLE_WINDOWS.items())
    body = "\n".join(f"<p>{block}</p>" for block in blocks)
    return (
        "<!DOCTYPE html>\n<html lang=\"ru\"><head><meta charset=\"utf-8\">"
        "<title>Системные окна: предпросмотр</title>"
        "<style>body{max-width:900px;margin:32px auto;padding:0 16px;font-family:Arial,sans-serif;"
        "font-size:16px;color:#333;background:#fff;} p{margin:0;}</style></head>\n"
        "<body>\n<p style=\"margin-bottom:16px;\">Так рамки выглядят в браузере. На Rulate шрифт и ширина "
        "колонки свои, цвета и отступы те же.</p>\n"
        f"{body}\n</body></html>\n"
    )
