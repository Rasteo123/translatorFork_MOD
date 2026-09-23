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
_TRAILING_PUNCT = ".!?…。！？"
_MAX_SPAN_PARAGRAPHS = 20
# Авторские и переводческие примечания в скобках и служебные пометки
# вроде «[Конец главы]», «(Конец главы)» — не системные окна. Эти исключения
# действуют всегда; поле страницы только добавляет к ним свои.
DEFAULT_EXCLUDE = (
    r"прим(\.|ечани\w*)\s*(автора|авт\.|пер\.|переводчика)|^[\[【(]?\s*P\.?\s?S\.?\b"
    r"|^\W*(конец главы|конец книги|продолжение следует)"
    r"|благодар\w*\s+(за\s+(донат|пожертв|поддержк|лунн|подар)|читател)"
)
_BUILTIN_EXCLUDE_RE = re.compile(DEFAULT_EXCLUDE, re.I)
# Прежние значения поля «Не трогать строки» по умолчанию. Сохранённое такое
# значение (и пустое поле первых версий) — не правило пользователя, а старая
# копия встроенных исключений.
LEGACY_EXCLUDE_DEFAULTS = (
    'прим(\\.|ечани\\w*)\\s*(автора|пер\\.|переводчика)|^[\\[【]?\\s*P\\.?\\s?S\\.?\\b',
    'прим(\\.|ечани\\w*)\\s*(автора|пер\\.|переводчика)|^[\\[【]?\\s*P\\.?\\s?S\\.?\\b|^[\\[【]?\\s*(конец главы|продолжение следует)',
    'прим(\\.|ечани\\w*)\\s*(автора|авт\\.|пер\\.|переводчика)|^[\\[【(]?\\s*P\\.?\\s?S\\.?\\b|^[\\[【]?\\s*(конец главы|продолжение следует)|благодар\\w*\\s+за\\s+(донат|пожертв|поддержк|лунн|подар)',
    'прим(\\.|ечани\\w*)\\s*(автора|авт\\.|пер\\.|переводчика)|^[\\[【(]?\\s*P\\.?\\s?S\\.?\\b|^[\\[【]?\\s*(конец главы|конец книги|продолжение следует)|благодар\\w*\\s+(за\\s+(донат|пожертв|поддержк|лунн|подар)|читател)',
)
_CLOSING_P_RE = re.compile(r"</p\s*>", re.I)
_OPENING_P_RE = re.compile(r"<p[\s>/]", re.I)


def user_exclude_pattern(saved) -> str:
    """Своё правило пользователя из сохранённого поля: старые значения по умолчанию — пусто."""
    saved = str(saved or "").strip()
    if not saved or saved == DEFAULT_EXCLUDE or saved in LEGACY_EXCLUDE_DEFAULTS:
        return ""
    return saved


@dataclass(frozen=True)
class DetectorSettings:
    triggers: tuple[str, ...] = DEFAULT_TRIGGERS
    single_bracketed: bool = True
    #: Свои исключения пользователя сверх встроенных ``DEFAULT_EXCLUDE``.
    exclude_pattern: str = ""


@dataclass
class WindowCandidate:
    start: int
    end: int
    kind: str
    lines: list[str]
    paragraph_html: list[str]
    #: Как найдена серия: brackets, quotes, pairs (ключ: значение) или source
    #: (по скобкам в исходнике, когда перевод их потерял).
    origin: str = "brackets"


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
    # Реплика после паузы: «…— Вы поразительны, господин Шарль».
    return text.lstrip("….").lstrip().startswith(("—", "–", "- "))


_QUOTE_CHARS = "«\"“„'"
_GARBAGE_LINES = frozenset({"***", "…", "...", "* * *"})
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
    "снаряжение питомец задание квест условие эффект описание тип стоимость длительность hp mp sp exp "
    "дух кольца кольцо культивация культивации техника техники атрибут атрибуты стихия свойство свойства "
    "характеристика характеристики богатство богатства баллы баллов репутация известность "
    "проворство заклинание заклинания".split()
    + ["боевой дух", "духовная сила", "духовные кольца", "духовное кольцо", "ранг духа", "боевая мощь", "очки системы"]
)
_STAT_VALUE_MAX = 300
_VALUE_MAX = 120
_QUOTED_KEY_RE = re.compile(r"^«[^»]{1,40}»\s*[:：]")
# Значение из скобок и имён в кавычках, в том числе пустых ячеек:
# «Магия: 【 】», «Магия: «Эйнсел», [ ], [ ].».
_NAMES_OR_CELLS_RE = re.compile(r"^(?:(?:«[^«»]*[^\W\d_][^«»]*»|[\[【〖][^\]】〗]*[\]】〗])[\s,;]*)+\.?$")
# Прирост: «I40 → I50», «lv2→lv3», «5 → 6».
_INCREMENT_RE = re.compile(r"[\w)]\s*(?:→|->|=>)\s*[\w(]")
# Рост уровня: «Lv7 → lv8!», «Ур. 1 → Ур. 2», «Повышение уровня: 5 → 6!».
_LEVEL_INCREMENT_RE = re.compile(
    r"(?:ур\.?|уровень|уровня|lv\.?|lvl\.?|level)\s*[:：]?\s*\d+\s*(?:→|->|=>)\s*"
    r"(?:ур\.?\s*|lv\.?\s*|lvl\.?\s*|level\s*)?\d",
    re.I,
)
_INCREMENT_LINE_MAX = 80
_KEY_QUOTES = "«»\"“”"
# Шапка книги и заголовки глав: «Автор: …», «Глава 9: Нина.» — не карточка статуса.
_META_KEYS = frozenset(
    "автор название переводчик перевод источник издательство аннотация оригинал жанр "
    "глава часть том пролог эпилог количество объем объём id просмотры просмотров просмотр "
    "год рейтинг лайков".split()
)


def _is_stat_key(key: str) -> bool:
    """Ключ-характеристика: не длиннее трёх слов, одно из них из списка.

    Четыре слова перед двоеточием («Награда оказалась весьма щедрой: …») —
    уже фраза, а не поле карточки.
    """
    words = re.findall(r"[^\W\d_]+", key.lower())
    if not words or len(words) > 3:
        return False
    return key.lower() in _STAT_KEYS or any(word in _STAT_KEYS for word in words)


_STAT_LINE_RE = re.compile(r"^([^:：]{1,40})[:：]\s*(\S.*)$")
_STAT_LINE_VALUE_MAX = 60


def _has_meta_word(key: str) -> bool:
    words = re.findall(r"[^\W\d_]+", key.lower())
    return any(
        word == meta or (len(meta) >= 5 and word.startswith(meta))
        for word in words
        for meta in _META_KEYS
    )


def is_single_stat_line(text: str) -> bool:
    """Одинокая строка показателя: ``Очки: 100.``, ``Здоровье: 22%``, ``Магия: «Бис».``.

    Такая строка сама по себе окно: интерфейс системы в прозе описывают по
    одной строке. Нужны известный ключ и короткое значение с цифрой, иначе это
    фраза вроде «Награда оказалась щедрой: помимо 2000 баллов…».
    """
    if not text or text[0] in _QUOTE_CHARS + "(—–-[【〖":
        return False
    match = _STAT_LINE_RE.match(text)
    if match is None:
        return False
    key = match.group(1).strip().strip(_KEY_QUOTES)
    value = match.group(2).strip()
    if not _is_stat_key(key) or len(value) > _VALUE_MAX:
        return False
    # Название главы и шапка книги: «Глава 302. Основное задание: …», «Имя автора: …».
    if _has_meta_word(key):
        return False
    # Короткое число или список имён в кавычках и ячеек: «Магия: «Потерянный Котенок».».
    if _NAMES_OR_CELLS_RE.match(value):
        return True
    return len(value) <= _STAT_LINE_VALUE_MAX and any(char.isdigit() for char in value)


# Шапка карточки кончается уровнем: «Шарль, Ур. 1.», «Альфия. Ур. 3.».
_LEVEL_END_RE = re.compile(r"(?:^|[\s,.;:–—-])(?:ур\.|уровень|lv\.?|lvl\.?|level)\s*\d+\s*\.?$", re.I)
_LEVEL_HEADER_MAX = 60
_LEVEL_NAME_WORDS_MAX = 3


def is_increment_line(text: str) -> bool:
    """Строка роста уровня: ``Lv7 → lv8!``, ``Повышение уровня: 5 → 6!``, ``Шарль: lv2→lv3.``.

    Сама по себе окно, как одинокая строка показателя. Цепочка рангов
    «S → A → B → … I0.» без уровня и чисел по обе стороны стрелки — нет.
    """
    if not text or len(text) > _INCREMENT_LINE_MAX or text[0] in _QUOTE_CHARS + "—–-([【〖":
        return False
    return _LEVEL_INCREMENT_RE.search(text) is not None


def is_level_header(text: str) -> bool:
    """Шапка карточки с уровнем: ``Шарль, Ур. 1.``, ``Зард, уровень 8.``.

    Сама по себе окна не делает: окно получится, если за ней идут строки
    карточки. Выкрик «Ур. 1 → Ур. 2 → Ур. 3!» шапкой не считается.
    """
    # Имя в кавычках допустимо: «Мудрец» (имя изменено), lv1.
    if not text or len(text) > _LEVEL_HEADER_MAX or text[0] in "\"“„'—–-([【〖":
        return False
    match = _LEVEL_END_RE.search(text)
    if match is None:
        return False
    # Перед уровнем — имя, а не фраза: «Таллис глянул на свой уровень – lv1.».
    name = re.sub(r"[:：]", " ", text[:match.start()])
    return len(re.findall(r"[^\W\d_]+", name)) <= _LEVEL_NAME_WORDS_MAX


_LABEL_RE = re.compile(r"^([^:：«»\"“”]{1,40})[:：]$")
_QUOTED_LABEL_RE = re.compile(r"^«([^«»:：.!?…]{1,40})[.:：]?»[.:：]?$")
_TERM_LABEL_RE = re.compile(r"^«([^«»]{1,60})»(?:\s*\([^()]{1,80}\))?\s*[:：]$")
# Одно слово раздела репликой: «— Характеристики…».
_DASHED_LABEL_RE = re.compile(r"^[—–-]\s*([^\W\d_]{3,24})\s*(?:…|\.\.\.|[:：.])?$")
_LABEL_WORDS_MAX = 2


def is_section_label(text: str) -> bool:
    """Заголовок раздела карточки: ``Навыки:``, ``«Магия.»``, ``«Имя навыка»:``.

    Ключ без кавычек должен быть характеристикой (``Шарль моргнул:`` — проза),
    имя в кавычках с двоеточием — это навык, за которым идут его пункты.
    """
    text = _BULLET_MARK_RE.sub("", text.strip(), count=1)
    if _TERM_LABEL_RE.match(text):
        return True
    match = _LABEL_RE.match(text) or _QUOTED_LABEL_RE.match(text) or _DASHED_LABEL_RE.match(text)
    if match is None:
        return False
    key = match.group(1).strip()
    # «Описание было кратким:» — уже фраза, а не заголовок раздела.
    return len(key.split()) <= _LABEL_WORDS_MAX and _is_stat_key(key)


# За маркером — слово, а не ещё маркеры: «······» и «* * *» — разделители.
_BULLET_RE = re.compile(r"^(?:[·•●▪◦‣∙]\s*|\*\s+)[\w«\"“„(\[【+−±-]")
_BULLET_MARK_RE = re.compile(r"^(?:[·•●▪◦‣∙]|\*(?=\s))\s*")
_BULLET_MAX = 400


def is_bullet_line(text: str) -> bool:
    """Пункт описания навыка: ``· Магия призыва.``, ``• Активируется при …``.

    Продолжает окно, но не начинает его: списки бывают и в прозе.
    """
    return len(text) <= _BULLET_MAX and _BULLET_RE.match(text) is not None


# Имя навыка отдельной строкой: «Тепло.», «Наследие Пегаса» (Реликвия Пегаса),
# «Крути до победного!» — часть окна, только если под ним пункты описания.
_QUOTED_TERM_RE = re.compile(r"^«[^«»]{1,60}»(?:\s*\([^()]{1,80}\))?[.!]?$")
# Шапка списка пунктов: «Нагрудник [«Скрытность» (2)]», «Руна 7 Таль + руна 5 Эт.».
_HEADING_MAX = 60
_VALUE_LINE_MAX = 80
_WIDE_LABEL_WORDS = 3
# Разделитель частей карточки: «…», «… …», «...».
_ELLIPSIS_LINE_RE = re.compile(r"^(?:…|\.\.\.)(?:\s*(?:…|\.\.\.))*$")
_NEXT_SENTENCE_RE = re.compile(r"[.!?…]\s+[A-ZА-ЯЁ]")


def _is_heading_line(text: str) -> bool:
    """Короткая строка над списком пунктов, не вводная фраза с двоеточием."""
    return (
        bool(text)
        and len(text) <= _HEADING_MAX
        and text not in _GARBAGE_LINES
        and not _is_dialogue(text)
        and not text.startswith("(")
        and not text.rstrip().endswith((":", "："))
        and any(char.isalpha() for char in text)
        and not is_bullet_line(text)
    )


# Шапка списка: строка в скобках или имя в кавычках, кончается двоеточием —
# «[Справочник рангов:]», ««Пожиратель Звёзд»:». Обычное «Навыки:» список не
# открывает: под ним берётся одна строка значения.
_LIST_HEADER_RE = re.compile(r"^(?:[\[【〖].*[:：]\s*[\]】〗]|«[^«»]+»(?:\s*\([^()]*\))?\s*[:：])$")
_LIST_ITEM_MAX = 120
_LIST_WORD_MAX = 30
_ENUMERATION_MAX = 600


def _opens_list(text: str) -> bool:
    return _LIST_HEADER_RE.match(_BULLET_MARK_RE.sub("", text.strip(), count=1)) is not None


def _is_short_item(text: str) -> bool:
    return (
        bool(text)
        and len(text) <= _LIST_WORD_MAX
        and len(text.split()) <= 3
        and not _is_dialogue(text)
        and not text.rstrip().endswith(("…", ":", "："))
    )


def _is_list_item(text: str, previous: str = "", following: str = "") -> bool:
    """Строка списка под шапкой: «Младший боец (сила удара 900 кг)», «Сила Доу.».

    Одно предложение с числом или скобками, перечень через запятые или короткая
    строка в столбике таких же («Сила Доу.», «Практик Доу.»). Одинокая короткая
    проза «Он кивнул.», реплики и «…» списка не продолжают.
    """
    if not text or _is_dialogue(text) or _ELLIPSIS_LINE_RE.match(text) or text.rstrip().endswith("…"):
        return False
    if _NEXT_SENTENCE_RE.search(text):
        return False
    if len(text) <= _LIST_ITEM_MAX and (any(char.isdigit() for char in text) or "(" in text):
        return True
    if _is_short_item(text):
        return _is_short_item(previous) or _is_short_item(following)
    return _is_enumeration(text)


_ENUMERATION_PARTS_MIN = 4
_ENUMERATION_PART_WORDS = 3


def _is_enumeration(text: str) -> bool:
    """Перечень через запятые: «Закалка Ци, заложение основ, формирование ядра, …».

    Части короткие, в два-три слова; во фразе прозы с запятыми они длиннее.
    """
    if len(text) > _ENUMERATION_MAX:
        return False
    parts = [part.strip() for part in text.rstrip(".").split(",")]
    return len(parts) >= _ENUMERATION_PARTS_MIN and all(
        0 < len(part.split()) <= _ENUMERATION_PART_WORDS for part in parts
    )


def _is_value_line(text: str) -> bool:
    """Строка сразу под заголовком раздела: «Колесо Чудес» / Miracle Wheel.

    Короткая и в одно предложение: проза под заголовком длиннее.
    """
    return (
        bool(text)
        and len(text) <= _VALUE_LINE_MAX
        and text not in _GARBAGE_LINES
        and not _is_dialogue(text)
        and not text.startswith("(")
        and _NEXT_SENTENCE_RE.search(text) is None
    )


def is_key_value(text: str) -> bool:
    """Строка вида ``Ключ: значение`` (или несколько таких через ``|``).

    Чат-реплики ``«Ник: текст»`` и сценарные реплики ``Имя: «…»`` / ``Имя: — …``
    сюда не попадают: они начинаются с кавычки или их значение начинается с
    кавычки либо тире. Точка в конце допустима для карточек статуса:
    ключ из известных характеристик или значение с заглавной буквы либо цифры.
    """
    inner = strip_brackets(text)
    if not inner or _is_dialogue(inner) or len(inner) > 400 or inner[0] == "(":
        return False
    if inner[0] in _QUOTE_CHARS and _QUOTED_KEY_RE.match(inner) is None:
        return False
    stripped = inner.rstrip()
    if stripped.endswith(("!", "?", ":")) and _INCREMENT_RE.search(inner) is None:
        return False
    # Точка или многоточие в конце: у карточек статуса это обычное дело,
    # у прозы с двоеточием («Он сказал: привет.») — нет.
    ends_with_period = stripped.endswith(".")
    ends_with_ellipsis = stripped.endswith("…")
    unbracketed = bracket_shape(text) is None
    parts = [part.strip() for part in inner.split("|")] if "|" in inner else [inner]
    for part in parts:
        if _KV_RE.match(part) is None:
            continue
        key, value = re.split(r"[:：]", part, maxsplit=1)
        key = key.strip().strip(_KEY_QUOTES)
        value = value.strip()
        stat_key = _is_stat_key(key)
        if not value or len(key.split()) > 4:
            continue
        # Значение с кавычки — сценарная реплика «Имя: «…»», но у известной
        # характеристики это имя навыка или магии: «Магия: «Золушка.»».
        if value[0] in _VALUE_BAD_START and not (stat_key and value[0] in _QUOTE_CHARS):
            continue
        if len(value) > (_STAT_VALUE_MAX if stat_key else _VALUE_MAX):
            continue
        if unbracketed and key.lower().split()[0] in _META_KEYS:
            continue
        if not (_mostly_alphanumeric(value) or _NAMES_OR_CELLS_RE.match(value)):
            continue
        # Прирост со стрелкой — данные при любом ключе: «Шарль: lv2→lv3.».
        if (ends_with_period or ends_with_ellipsis) and not stat_key and _INCREMENT_RE.search(value) is None:
            numeric = any(char.isdigit() for char in value)
            if ends_with_ellipsis or not (value[0].isupper() or value[0].isdigit()):
                continue
            if len(value) > 40 and not numeric:
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
    return (
        bracket_shape(text) in _DATA_SHAPES
        or is_key_value(text)
        or is_single_stat_line(text)
        or _is_decorated(text)
        or is_level_header(text)
        or is_increment_line(text)
        or is_section_label(text)
        or is_bullet_line(text)
    )


def classify_kind(lines: list[str]) -> str:
    """Тип рамки: повышение уровня, затем карточка с двумя и более парами
    ``Ключ: значение`` (статус даже при слове «титул» внутри), затем по словам."""
    texts = [strip_brackets(line) for line in lines]
    joined = " ".join(texts)
    key_value_count = sum(1 for text in texts if is_key_value(text) or is_single_stat_line(text))
    rules = dict(_KIND_RULES)
    if rules["levelup"].search(joined) or _LEVEL_INCREMENT_RE.search(joined):
        return "levelup"
    if key_value_count >= 2 or (texts and key_value_count == len(texts)) or any(map(is_level_header, texts)):
        return "status"
    for kind, pattern in _KIND_RULES[1:]:
        if pattern.search(joined):
            return kind
    return DEFAULT_KIND


# --- поиск серий ------------------------------------------------------------

def source_paragraphs(html: str) -> list[str]:
    """Абзацы исходной главы: ``<p>``, а без них — текст между ``<br>``."""
    soup = BeautifulSoup(html, "html.parser")
    body = soup.find("body") or soup
    for heading in body.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        heading.decompose()
    tags = body.find_all("p")
    if tags:
        texts = [" ".join(tag.get_text(" ", strip=True).split()) for tag in tags]
        return [text for text in texts if text]
    for br in body.find_all("br"):
        br.replace_with("\n")
    return [" ".join(line.split()) for line in body.get_text("\n").split("\n") if line.strip()]


def _is_source_system_line(text: str) -> bool:
    return bool(text) and text[0] in "【[〖" and _outer_group_spans_all(text)


def _plausible_counterpart(target: str, source_text: str) -> bool:
    """Абзац перевода мог быть переводом этих строк исходника."""
    if target.startswith(("—", "–", "- ")) or target in _GARBAGE_LINES:
        return False
    if sum(1 for char in target if char.isalpha()) < 2:
        return False
    source_length = max(1, len("".join(source_text.split())))
    target_length = len("".join(target.split()))
    return 0.2 * source_length <= target_length <= 6 * source_length + 20


def source_marked_indices(html: str, source_html: str) -> dict[str, set[int]]:
    """Номера абзацев перевода напротив строк исходника в скобках, по семействам скобок.

    Абзацы выравниваются по длинам, как в проверке качества. Семейство
    (``[``, ``【`` или ``〖``) важно: в одной книге 【】 — это система, в другой —
    мысленная речь, и решать, каким верить, надо по всей книге.
    """
    from ..qa.source_pairing import _align_by_length, _visible_length

    source = source_paragraphs(source_html)
    targets = [(index, paragraph.text) for index, paragraph in enumerate(_paragraphs(html)) if paragraph.text]
    if not source or not targets:
        return {}
    spans = _align_by_length(
        [_visible_length(text) for text in source],
        [_visible_length(text) for _index, text in targets],
    )
    marked: dict[str, set[int]] = {}
    for source_start, source_end, target_start, target_end in spans:
        if source_start == source_end or target_start == target_end:
            continue
        source_lines = source[source_start:source_end]
        if not all(_is_source_system_line(line) for line in source_lines):
            continue
        family = source_lines[0][0]
        source_text = " ".join(source_lines)
        for index, text in targets[target_start:target_end]:
            if _plausible_counterpart(text, source_text):
                marked.setdefault(family, set()).add(index)
    return marked


_CHAT_LINE_RE = re.compile(r"^[^«:：]{1,30}[:：]\s*«")
_POLITE_RE = re.compile(
    r"(?<![\w-])(?:вы|вас|вам|вами|ваш|ваша|ваше|ваши|вашего|вашей|вашему|вашим|вашими|вашу|ваших)(?![\w-])",
    re.I,
)


# Приращения и доли: «сила рук +1», «Здоровье 90%», «81/90».
_STAT_DELTA_RE = re.compile(r"[+＋]\s?\d|\d\s?%|\d\s?/\s?\d")
# Итоги проверок и наград в начале строки или после паузы:
# «Проверка „Преследования“… Успех!», «Получено: …». Лозунг «залог успеха» — нет.
_CHECK_WORDS_RE = re.compile(r"(?:^|…\s*)(?:провер|успе[хш]|неудач|провал|получен|выполнен)\w*", re.I)
# Система о себе в первом лице не говорит, переписка и речь — говорят.
_FIRST_PERSON_RE = re.compile(
    r"(?<![\w-])(?:я|мне|меня|мной|мы|нам|нас|нами|мой|моя|моё|мое|мои|моего|моей|моему|моим|моих|мою|"
    r"наш|наша|наше|наши|нашего|нашей|нашему|нашим|нашими|наших|нашу)(?![\w-])",
    re.I,
)


def _source_confirms(text: str) -> bool:
    """Строка без скобок напротив скобок исходника сама похожа на систему.

    Скобки исходника — только признак особого текста. В «Рефреше» так
    выделены имена заклинаний и песнопения, в «Полоске здоровья» ещё новости,
    лозунги и чат; переводчик передал их кавычками. Окном такая строка
    станет, если систему видно по ней самой: пары «ключ: значение», начало
    вроде «Динь!», приращения вроде «+1» и «90%», итоги проверок и наград,
    обращение к герою на «вы» без «я» и «мы» (иначе это переписка или речь).
    """
    if not text or _CHAT_LINE_RE.match(text) or _is_dialogue(text) or text.rstrip().endswith((":", "：")):
        return False
    inner = _strip_outer_quotes(text).strip()
    if _SYSTEM_START_RE.match(inner) and _has_real_words(inner):
        return True
    if is_key_value(inner) or is_single_stat_line(inner):
        return True
    if _STAT_DELTA_RE.search(inner) or _CHECK_WORDS_RE.search(inner):
        return True
    return _POLITE_RE.search(inner) is not None and _FIRST_PERSON_RE.search(inner) is None


_TRUST_KEPT_MIN = 0.25
_TRUST_KEPT_MAX = 0.7


def trusted_source_families(kept_counts) -> set[str]:
    """Семейства скобок исходника, по которым стоит отмечать строки без скобок.

    ``kept_counts``: семейство → (сколько отмеченных абзацев перевода сами в
    скобках, сколько отмечено всего). Если перевод никогда не оставляет эти
    скобки, в исходнике они значат не систему, а, скажем, мысленную речь. Если
    оставляет почти всегда, немногие строки без скобок переводчик счёл речью в
    тех же скобках. Сверка помогает посередине: переводчик был непоследователен,
    и исходник знает лучше. Семейство без потерь доверенное: добавлять нечего.
    """
    trusted = set()
    for family, (kept, total) in kept_counts.items():
        if total <= 0:
            continue
        share = kept / total
        if kept == total or _TRUST_KEPT_MIN <= share <= _TRUST_KEPT_MAX:
            trusted.add(family)
    return trusted


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


def _origin(shape, marked: bool, text: str) -> str:
    if shape in ("full", "keyed", "list", "open", "dashed"):
        return "brackets"
    if shape == "quoted":
        return "quotes"
    if marked:
        return "source"
    return "pairs"


def find_windows(
    html: str,
    settings: DetectorSettings | None = None,
    source_html: str | None = None,
    source_marks: set[int] | None = None,
) -> list[WindowCandidate]:
    """Найти серии системных строк в HTML главы, в порядке документа.

    С ``source_html`` строки, напротив которых в исходнике стоят скобки любого
    семейства, считаются системными и без скобок в переводе; ``source_marks``
    передаёт уже отобранные номера абзацев (см. :func:`scan_chapters`).
    """
    settings = settings or DetectorSettings()
    extra_exclude = re.compile(settings.exclude_pattern, re.I) if settings.exclude_pattern else None
    paragraphs = _paragraphs(html)
    if source_marks is not None:
        marked = set(source_marks)
    elif source_html:
        marked = set().union(*source_marked_indices(html, source_html).values())
    else:
        marked = set()

    def excluded(paragraph: _Paragraph) -> bool:
        text = paragraph.text
        return (
            not text
            or _BUILTIN_EXCLUDE_RE.search(text) is not None
            or (extra_exclude is not None and extra_exclude.search(text) is not None)
        )

    def confirmed(position: int) -> bool:
        return position in marked and _source_confirms(paragraphs[position].text)

    def bullet_at(position: int) -> bool:
        return (
            position < len(paragraphs)
            and paragraphs[position].adjacent
            and is_bullet_line(paragraphs[position].text)
        )

    def term_with_bullets(position: int) -> bool:
        return _QUOTED_TERM_RE.match(paragraphs[position].text) is not None and bullet_at(position + 1)

    def bullet_run(position: int) -> bool:
        """Два пункта подряд — список эффектов: окно и без шапки."""
        return is_bullet_line(paragraphs[position].text) and bullet_at(position + 1)

    def heads_bullets(position: int, depth: int = 0) -> bool:
        """Одна-две короткие строки прямо над списком пунктов — его шапка."""
        following = position + 1
        if not _is_heading_line(paragraphs[position].text) or following >= len(paragraphs):
            return False
        if not paragraphs[following].adjacent:
            return False
        if bullet_run(following):
            return True
        # Вторая строка шапки — не данные: со строки карточки окно начнётся и так,
        # а фраза прозы над ней («Он вытянул несколько способностей.») в окно не нужна.
        if depth or _is_data_line(paragraphs[following].text):
            return False
        return heads_bullets(following, depth=1)

    def wide_label(position: int) -> bool:
        """«Способности Пищевой Цепи:» — три слова, но под ним список имён или пунктов."""
        match = _LABEL_RE.match(paragraphs[position].text.strip())
        if match is None:
            return False
        key = match.group(1).strip()
        words = key.lower().split()
        following = position + 1
        # Характеристика — первое слово: «Зажглась аномальная способность:» — проза.
        return (
            len(words) <= _WIDE_LABEL_WORDS
            and words[0] in _STAT_KEYS
            and following < len(paragraphs)
            and paragraphs[following].adjacent
            and (
                _QUOTED_TERM_RE.match(paragraphs[following].text) is not None
                or is_bullet_line(paragraphs[following].text)
            )
        )

    def data_follows(position: int) -> bool:
        following = position + 1
        if following >= len(paragraphs) or not paragraphs[following].adjacent or excluded(paragraphs[following]):
            return False
        return (
            _is_data_line(paragraphs[following].text)
            or wide_label(following)
            or term_with_bullets(following)
            or heads_bullets(following)
        )

    def continues(position: int) -> bool:
        text = paragraphs[position].text
        previous = paragraphs[position - 1].text
        if (
            confirmed(position)
            or _is_data_line(text)
            or term_with_bullets(position)
            or heads_bullets(position)
            or wide_label(position)
        ):
            return True
        # «…» внутри карточки: окно идёт дальше, если за разделителем снова данные.
        if _ELLIPSIS_LINE_RE.match(text):
            return data_follows(position)
        if (is_section_label(previous) or wide_label(position - 1)) and _is_value_line(text):
            return True
        # Имена навыков столбиком под заголовком: «Казан Души Клинка.», «Аура Шипов.».
        return _QUOTED_TERM_RE.match(text) is not None and _QUOTED_TERM_RE.match(previous) is not None

    windows: list[WindowCandidate] = []
    index = 0
    while index < len(paragraphs):
        first = paragraphs[index]
        text = first.text
        if excluded(first):
            index += 1
            continue

        shape = bracket_shape(text)
        is_marked = confirmed(index)
        stop = index + 1
        if shape == "open":
            span_end = _span_end(paragraphs, index, excluded)
            if span_end is None:
                index += 1
                continue
            stop = span_end + 1
        single_stat = is_single_stat_line(text)
        if shape == "open":
            pass
        elif not (
            shape in _DATA_SHAPES
            or is_marked
            or single_stat
            or _is_header(text, settings)
            or is_key_value(text)
            or is_level_header(text)
            or is_increment_line(text)
            or is_section_label(text)
            or term_with_bullets(index)
            or bullet_run(index)
            or heads_bullets(index)
            or wide_label(index)
        ):
            index += 1
            continue

        # После шапки с двоеточием окно продолжают строки списка.
        in_list = _opens_list(paragraphs[stop - 1].text)
        while (
            stop < len(paragraphs)
            and paragraphs[stop].adjacent
            and not excluded(paragraphs[stop])
        ):
            following = paragraphs[stop].text
            after = paragraphs[stop + 1].text if stop + 1 < len(paragraphs) and paragraphs[stop + 1].adjacent else ""
            if not (continues(stop) or (in_list and _is_list_item(following, paragraphs[stop - 1].text, after))):
                break
            in_list = in_list or _opens_list(following)
            stop += 1

        while stop - 1 > index and _ELLIPSIS_LINE_RE.match(paragraphs[stop - 1].text):
            stop -= 1
        length = stop - index
        single_stat = single_stat or is_increment_line(text)
        accepted = length >= 2 or single_stat or ((shape in _SINGLE_SHAPES or is_marked) and settings.single_bracketed)
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
                origin=_origin(shape, is_marked, text),
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


_OUTER_QUOTES_RE = re.compile(r"^«([^«»]+)»([.!?…]*)$")


def _strip_outer_quotes(text: str) -> str:
    """«Метка»! → Метка!: строка целиком в кавычках внутри окна — сообщение."""
    match = _OUTER_QUOTES_RE.match(text)
    return match.group(1).strip() + match.group(2) if match else text


def _render_row(text: str, accent: str, italic_allowed: bool):
    """Строки окна: [(html, короткая ли это пара ключ-значение для колонок)]."""
    if bracket_shape(text) == "keyed":
        return [(_render_keyed(text, accent), False)]
    if is_section_label(text):
        label = _BULLET_MARK_RE.sub("", text.strip(), count=1)
        label = re.sub(r"^[—–-]\s*", "", label).rstrip(":：…").strip()
        # «Пламенная Душа» (пассивный навык) → Пламенная Душа (пассивный навык)
        label = re.sub(r"^«([^«»]+)»", r"\1", label).rstrip(".").strip()
        return [(f'<b style="color:{accent};">{_escape(label)}:</b>', False)]
    text = _strip_outer_quotes(strip_brackets(text))
    if is_key_value(text):
        parts = _stat_parts(text)
        # Приросты и ранги пишут столбиком, как статусы в DanMachi: по одному в строке.
        if len(parts) > 1 and all(_KV_RE.match(part) and _is_column_value(part) for part in parts):
            return [(_render_key_value_part(part, accent), False) for part in parts]
        rendered = []
        for part in parts:
            if _KV_RE.match(part):
                rendered.append(_render_key_value_part(part, accent))
            else:
                rendered.append(_escape(part))
        value = re.split(r"[:：]", text, maxsplit=1)[1].strip()
        short = len(parts) == 1 and len(value) <= _SHORT_VALUE and not _is_column_value(text)
        return [(_SEPARATOR.join(rendered), short)]
    escaped = _escape(text)
    if italic_allowed and len(text) >= _ITALIC_FROM:
        return [(f"<i>{escaped}</i>", False)]
    return [(escaped, False)]


# Ранг и число, как в статусах DanMachi: «F358», «I 0», «SSS 2101», «EX 7774».
# Кириллические А, В, Е, Н, С — двойники латинских: переводчик мог набрать их.
_RANK_VALUE_RE = re.compile(r"^(?:[A-ZАВЕНС]{1,3}|EX|MAX)\s?\d{1,5}\.?$")


def _is_column_value(part: str) -> bool:
    """Показатель, который пишут столбиком: прирост со стрелкой или ранг с числом."""
    value = re.split(r"[:：]", part, maxsplit=1)[1].strip() if _KV_RE.match(part) else part
    return _INCREMENT_RE.search(value) is not None or _RANK_VALUE_RE.match(value) is not None


def _stat_parts(text: str) -> list[str]:
    """Пары одной строки: через ``|`` или через `` / ``, если каждая часть — пара."""
    if "|" in text:
        return [part.strip() for part in text.split("|")]
    pieces = [part.strip() for part in re.split(r"\s+/\s+", text)]
    if len(pieces) > 1 and all(_KV_RE.match(piece) for piece in pieces):
        return pieces
    return [text]


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
        texts[0] = _strip_outer_quotes(strip_brackets(texts[0]))
    title = None
    if len(texts) >= 2 and _looks_like_title(texts[0]):
        title, texts = _strip_outer_quotes(texts[0].rstrip(":：").strip()), texts[1:]

    rows = [row for text in texts for row in _render_row(text, accent, italic_allowed=title is not None)]
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
# BeautifulSoup (им пересобирает главы сборка EPUB) пишет значение с двойными
# кавычками внутри в одинарных кавычках.
_ORIG_RE = re.compile(r"""\bdata-sys-orig=(?:"([^"]*)"|'([^']*)')""")
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
        value = original.group(1) if original.group(1) is not None else original.group(2)
        return html_module.unescape(value)

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


def _archive_text_is_russian(archive, names, originals) -> bool:
    """Первая же глава архива в основном кириллицей — это перевод, не исходник."""
    for name in originals:
        if name not in names:
            continue
        try:
            text = archive.read(name).decode("utf-8", "ignore")
        except (OSError, KeyError):
            continue
        letters = [char for char in re.sub(r"<[^>]+>", " ", text) if char.isalpha()]
        if len(letters) < 40:
            continue
        cyrillic = sum(1 for char in letters if "\u0400" <= char <= "\u04ff")
        return cyrillic * 2 > len(letters)
    return False


def find_source_epub(project_folder) -> str | None:
    """Исходный EPUB в папке проекта.

    В папке обычно несколько EPUB: исходник, переводы, редакции, пробы.
    Исходник содержит главы из карты перевода, написан не по-русски и самый
    ранний по дате файла.
    """
    import json
    import zipfile

    folder = str(project_folder)
    try:
        names = sorted(os.listdir(folder))
        with open(os.path.join(folder, "translation_map.json"), encoding="utf-8") as handle:
            originals = sorted(str(key).replace("\\", "/") for key in json.load(handle))
    except (OSError, ValueError):
        return None
    if not originals:
        return None
    wanted = set(originals)
    candidates = []
    for name in names:
        if not name.lower().endswith(".epub"):
            continue
        path = os.path.join(folder, name)
        try:
            with zipfile.ZipFile(path) as archive:
                present = wanted.intersection(archive.namelist())
                if len(present) * 2 < len(wanted):
                    continue
                if _archive_text_is_russian(archive, present, originals):
                    continue
            lowered = name.lower()
            translated_name = any(mark in lowered for mark in ("(ru)", "перевод", "translated", "тест"))
            # Самый ранний по дате; при равных датах — имя без пометок перевода.
            candidates.append((os.path.getmtime(path), translated_name, name, path))
        except (OSError, zipfile.BadZipFile):
            continue
    if not candidates:
        return None
    return min(candidates)[3]


def scan_chapters(entries, settings: DetectorSettings | None = None, progress=None) -> list[ChapterScan]:
    """Найти окна в главах ``(original, path, html, source_html | None)``.

    Сверка с исходником идёт в два прохода: сначала по всей книге считается,
    какие семейства скобок исходника перевод сохраняет, потом по доверенным
    семействам отмечаются строки без скобок.
    """
    entries = list(entries)
    total = len(entries)
    has_source = any(entry[3] for entry in entries)
    steps = 2 * total if has_source else total
    marks_by_chapter: list[dict[str, set[int]]] = []
    kept_counts: dict[str, list[int]] = {}
    for index, (original, _path, html, source_html) in enumerate(entries, start=1):
        marks = source_marked_indices(html, source_html) if source_html else {}
        marks_by_chapter.append(marks)
        if marks:
            texts = {position: paragraph.text for position, paragraph in enumerate(_paragraphs(html))}
            for family, indices in marks.items():
                counter = kept_counts.setdefault(family, [0, 0])
                for position in indices:
                    counter[1] += 1
                    if texts.get(position, "")[:1] in "[【〖":
                        counter[0] += 1
        if progress is not None and has_source:
            progress(index, steps, original)
    trusted = trusted_source_families({family: tuple(pair) for family, pair in kept_counts.items()})
    result = []
    for index, ((original, path, html, _source_html), marks) in enumerate(zip(entries, marks_by_chapter), start=1):
        source_marks = set().union(*(indices for family, indices in marks.items() if family in trusted)) if marks else set()
        result.append(ChapterScan(
            original=original,
            path=path,
            title=_chapter_title(html, original),
            candidates=find_windows(html, settings, source_marks=source_marks if marks else None),
        ))
        if progress is not None:
            progress((total if has_source else 0) + index, steps, original)
    return result


def scan_project(
    project_folder,
    settings: DetectorSettings | None = None,
    progress=None,
    source_epub: str | None = None,
) -> list[ChapterScan]:
    """Найти системные окна во всех главах проекта (и главы без окон тоже).

    С ``source_epub`` главы сверяются с исходником: строки, стоящие напротив
    скобок оригинала, тоже считаются системными (см. :func:`scan_chapters`).
    """
    import zipfile

    chapters = project_chapter_files(project_folder)
    archive = None
    names: set[str] = set()
    if source_epub and os.path.isfile(source_epub):
        try:
            archive = zipfile.ZipFile(source_epub)
            names = set(archive.namelist())
        except (OSError, zipfile.BadZipFile):
            archive = None
    entries = []
    try:
        for original, path in chapters:
            html = Path(path).read_bytes().decode("utf-8")
            source_html = None
            if archive is not None and original in names:
                source_html = archive.read(original).decode("utf-8", "ignore")
            entries.append((original, path, html, source_html))
    finally:
        if archive is not None:
            archive.close()
    return scan_chapters(entries, settings, progress)


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
