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
from dataclasses import dataclass, replace
from pathlib import Path

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from .io_utils import atomic_write_text
from .translation_versions import select_target_translation_version

BLOCK_ATTR = "data-sys"

_BRACKET_PAIRS = {"[": "]", "【": "】", "〖": "〗"}
# Угловые скобки бывают только вокруг строки целиком («< Навык получен>»).
_ANGLE_PAIRS = {"<": ">", "＜": "＞"}
_WORD_RE = re.compile(r"[^\W\d_]{2,}")
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
    r"|^\W*(конец главы|конец книги|конец арки|следующая арка|продолжение следует)"
    r"|благодар\w*\s+(за\s+(донат|пожертв|поддержк|лунн|подар)|читател)"
    r"|^\[/?(spoiler|b|i|u|s|quote|indent|center|size|color|url|img)(=[^\]]*)?\]$"
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
    #: Собеседники, уже узнанные в переписке этой книги (см. :func:`scan_chapters`):
    #: с ними чатом считается и серия без шапки и без повторов.
    chat_participants: frozenset = frozenset()
    #: Главы книги открываются скобками-заголовками сцены («[Эми Даллон 03]»,
    #: «[Мемориальный парк, Броктон-Бэй]»): такие строки в начале главы — не окна.
    chapter_headers: bool = False
    #: Скобками в книге записана речь (мысленная, телепатия): строка в скобках
    #: без системных слов — не окно.
    speech_brackets: bool = False


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
    #: Чей аккаунт открыт в переписке, по тексту перед ней («телефон Рена»,
    #: «Макото достала телефон», «Сообщение отправлено: Кен»); пусто — не ясно.
    chat_owner: str = ""
    #: Выбор на странице: чьи реплики справа. ``None`` — владелец или читающий книги.
    readers: tuple | None = None


@dataclass
class _Paragraph:
    start: int
    end: int
    text: str
    adjacent: bool
    #: Сосед предыдущего абзаца только через обёртки (``</div><div>``), не брат.
    wrapped: bool = False


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


# BB-код из исходника фанфика внутри строки: «[b]Имя:[/b] Вельф», «[spoiler]».
# Однобуквенные теги — только парой: «[B]», «[S]» бывают рангами.
_BBCODE_PAIR_RE = re.compile(r"\[(b|i|u|s)\](.*?)\[/\1\]", re.I | re.S)
_BBCODE_TAG_RE = re.compile(
    r"\[/?(?:indent|center|right|left|quote|url|img|color|size|spoiler|sub|sup|font)(?:=[^\]]*)?\]", re.I
)


def _strip_bbcode(text: str) -> str:
    for _depth in range(3):  # вложенные пары: «[b]Конец страницы. [u]1[/u][/b]»
        text, count = _BBCODE_PAIR_RE.subn(r"\2", text)
        if not count:
            break
    return _BBCODE_TAG_RE.sub(" ", text)


_WRAP_TAGS = frozenset({"div", "span", "section", "article"})
_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)


def _wrap_gap(gap: str) -> bool:
    """Между абзацами одни обёртки (``</div></div><div><div>``) без текста."""
    gap = _COMMENT_RE.sub("", gap)
    if _TAG_RE.sub("", gap).strip():
        return False
    names = [match.group(2).lower() for match in _TAG_TOKEN_RE.finditer(gap)]
    return bool(names) and all(name in _WRAP_TAGS for name in names)


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
        text = " ".join(_strip_bbcode(tag.get_text(" ", strip=True)).split())
        sibling = previous_tag is not None and _next_element(previous_tag) is tag
        # Страницы веб-новелл кладут каждый абзац в свои div: такие абзацы
        # идут подряд, хотя и не братья (см. :func:`_window_pieces`).
        wrapped = not sibling and previous_tag is not None and _wrap_gap(raw[result[-1].end:start])
        result.append(_Paragraph(start=start, end=end, text=text, adjacent=sibling or wrapped, wrapped=wrapped))
        previous_tag = tag
    return result


# --- классификация строк ----------------------------------------------------

_FULL_RE = re.compile(r"^([\[【〖<＜])(.*)([\]】〗>＞])([.!?…]*)$", re.S)
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
    if opening in _ANGLE_PAIRS:
        # «< Навык [Память] достиг уровня 2>»: уведомление целиком в угловых
        # скобках. Одно слово («<Хр-р-р>») — звук, смайлик без букв — не окно.
        closing = _ANGLE_PAIRS[opening]
        body = text.rstrip(_TRAILING_PUNCT)
        inner = body[1:-1] if body.endswith(closing) else ""
        if inner and opening not in inner and closing not in inner and len(_WORD_RE.findall(inner)) >= 2:
            return "full"
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
    "проворство заклинание заклинания удача качество слот блокирование уклонение сопротивление "
    "меткость точность".split()
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
    "опубликовано завершено обновлено слов "
    "глава часть том пролог эпилог количество объем объём id просмотры просмотров просмотр "
    "год рейтинг лайков".split()
)


# Бонус комплекта снаряжения: «2 вещи: …», «4 предмета: …».
_SET_BONUS_KEY_RE = re.compile(r"^\d+\s+(?:вещ|предмет|част|элемент)[а-яё]*$", re.I)
# Значение-число со знаком: «+2.», «-10%», «−5».
_SIGNED_NUMBER_RE = re.compile(r"^[+＋\-−–]\s?\d")


def _is_stat_key(key: str) -> bool:
    """Ключ-характеристика: не длиннее трёх слов, одно из них из списка.

    Четыре слова перед двоеточием («Награда оказалась весьма щедрой: …») —
    уже фраза, а не поле карточки.
    """
    if _SET_BONUS_KEY_RE.match(key.strip()):
        return True
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


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+(?=[A-ZА-ЯЁ])")


def _is_item_sentence(text: str) -> bool:
    """Пункт перечня: одно предложение с числом или пояснением в скобках."""
    return len(text) <= _LIST_ITEM_MAX and (any(char.isdigit() for char in text) or "(" in text)


def _is_list_item(text: str, previous: str = "", following: str = "") -> bool:
    """Строка списка под шапкой: «Младший боец (сила удара 900 кг)», «Сила Доу.».

    Одно предложение с числом или скобками, перечень через запятые или короткая
    строка в столбике таких же («Сила Доу.», «Практик Доу.»). Одинокая короткая
    проза «Он кивнул.», реплики и «…» списка не продолжают.
    """
    if not text or _is_dialogue(text) or _ELLIPSIS_LINE_RE.match(text) or text.rstrip().endswith("…"):
        return False
    if _NEXT_SENTENCE_RE.search(text):
        # Две ступени в одной строке: «Пик почтенного Доу (с первого по десятый
        # ранги), выход за грани смертного. Полусвятой (младший, средний уровни).»
        sentences = [part.strip() for part in _SENTENCE_SPLIT_RE.split(text) if part.strip()]
        return len(sentences) >= 2 and all(_is_item_sentence(sentence) for sentence in sentences)
    if _is_item_sentence(text):
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
        if (
            value[0] in _VALUE_BAD_START
            and not (stat_key and value[0] in _QUOTE_CHARS)
            and _SIGNED_NUMBER_RE.match(value) is None
        ):
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
            signed = _SIGNED_NUMBER_RE.match(value) is not None
            if ends_with_ellipsis or not (value[0].isupper() or value[0].isdigit() or signed):
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


# --- переписка ----------------------------------------------------------------

# Реплика чата в переводе: «[Кен]: текст» (Ace in the Hole), «Дядя Ли: «текст»»
# («Бизнес с карточками», «Полоска здоровья»), ««Хаоюгэн: текст»» (форум
# «Возрождения»), «Сье: текст» (LOL).
_CHAT_NAME = r"[^\W\d_][\w'’ -]{0,29}?"
_CHAT_BRACKET_RE = re.compile(rf"^[\[【]({_CHAT_NAME})[\]】]\s*[:：]\s*(\S.*)$")
_CHAT_FORUM_RE = re.compile(rf"^«({_CHAT_NAME})\s*[:：]\s*(.+?)»([.!?…]*)$")
_CHAT_QUOTED_RE = re.compile(rf"^({_CHAT_NAME})\s*[:：]\s*«(.+)»([.!?…]*)$")
_CHAT_BARE_RE = re.compile(rf"^({_CHAT_NAME})\s*[:：]\s*([^\s«\[【—–\-(].*)$")
# Шапка переписки: «Групповой чат: Бывшие SEES», «Сообщение от: Макото.».
# Проза вроде «Чат мгновенно затих» или «Сообщение от Бэй Жу напомнило…» — не шапка.
_CHAT_HEADER_RE = re.compile(
    r"^[\[【]?(?:групповой чат|общий чат|группа|чат|переписка|(?:личное |новое |входящее )?сообщение"
    r"(?:\s+(?:от|для|отправлено|получено))?)\s*(?:[:：]|«)",
    re.I,
)
_CHAT_HEADER_MAX = 100
_CHAT_NAME_WORDS_MAX = 3
# Живая речь: вопрос, восклицание, «я/ты/вы», «привет», «спасибо». Описание
# навыка в «[Метание]: Бросает камни…» так не звучит.
_CONVERSATIONAL_RE = re.compile(
    r"[?!]|(?<![\w-])(?:я|ты|вы|мы|мне|меня|тебя|тебе|вас|вам|нас|нам|мой|моя|моё|мое|мои|твой|твоя|твоё|"
    r"твои|ваш|ваша|наш|наша|привет|спасибо|ладно|окей|хорошо|да|нет|ну)(?![\w-])",
    re.I,
)


@dataclass(frozen=True)
class ChatLine:
    speaker: str
    message: str
    #: bracket — «[Кен]: …», quoted — «Кен: «…»», forum — ««Кен: …»», bare — «Кен: …».
    style: str


def is_chat_header(text: str) -> bool:
    text = text.strip()
    return bool(text) and len(text) <= _CHAT_HEADER_MAX and _CHAT_HEADER_RE.match(text) is not None


def chat_line(text: str) -> ChatLine | None:
    """Реплика переписки ``Имя + сообщение`` или ``None``."""
    stripped = " ".join(text.split())
    if not stripped or is_chat_header(stripped):
        return None
    for style, pattern in (
        ("bracket", _CHAT_BRACKET_RE),
        ("forum", _CHAT_FORUM_RE),
        ("quoted", _CHAT_QUOTED_RE),
        ("bare", _CHAT_BARE_RE),
    ):
        match = pattern.match(stripped)
        if match is None:
            continue
        speaker = match.group(1).strip()
        message = match.group(2).strip()
        if style in ("forum", "quoted"):
            message += match.group(3)
        if not message or len(speaker.split()) > _CHAT_NAME_WORDS_MAX:
            return None
        return ChatLine(speaker, message, style)
    return None


# Не имена собеседников: «Вопрос: … / Ответ: …», «Например: … / Или: …».
_NOT_SPEAKERS = frozenset(
    "вопрос ответ например или новое примечание внимание итог итоги совет подсказка цель задача причина "
    "следствие плюс минус первое второе третье шаг вывод результат замечание пример важно кстати "
    "кто что где когда как почему зачем куда откуда чей далее затем потом итак сначала наконец "
    "следующая следующий следующее следующие предыдущая предыдущий улика".split()
)


def _looks_like_name(name: str, *, nickname: bool = False) -> bool:
    """Имя собеседника: одно-три слова с заглавной, не характеристика и не «Система».

    Ник на форуме («учительница Ли», «Любитель яичницы») может начинаться со
    строчной буквы и продолжаться строчными словами.
    """
    words = name.split()
    if not 1 <= len(words) <= _CHAT_NAME_WORDS_MAX:
        return False
    if not nickname and any(not word[0].isupper() for word in words):
        return False
    lowered = name.lower()
    if lowered in _NOT_SPEAKERS or words[0].lower() in _NOT_SPEAKERS:
        return False
    return not (_is_stat_key(name) or _has_meta_word(name) or any(key in lowered for key in _TITLE_KEYS))


def _chat_messages(lines):
    """(число шапок, реплики) или ``None``, если в серии есть не реплика."""
    headers = 0
    messages: list[ChatLine] = []
    for line in lines:
        if is_chat_header(line):
            headers += 1
            continue
        parsed = chat_line(line)
        if parsed is None:
            return None
        messages.append(parsed)
    return headers, messages


def chat_verdict(lines, participants=frozenset()) -> str | None:
    """``"chat"``, ``"weak"`` (похоже на чат, но собеседники ещё не узнаны) или ``None``.

    Форма «[Кен]: …» совпадает с системным «[Термин]: значение», а ««Ник: …»» —
    с перечнем предметов в кавычках. Поэтому чатом серия считается при шапке
    («Групповой чат: …») или когда кто-то пишет в ней дважды. Серию без повтора
    («[Кен]: Кто это?» / «[Неизвестная]: Хех…») примут, если все её собеседники
    уже переписывались в других местах книги.
    """
    parsed = _chat_messages(lines)
    if parsed is None:
        return None
    headers, messages = parsed
    if not messages:
        return None
    if any(not _looks_like_name(item.speaker, nickname=item.style == "forum") for item in messages):
        return None
    speakers = [item.speaker for item in messages]
    # Без кавычек вокруг текста и с ником в кавычках так же пишут перечни
    # («Например: … / Или: …», ««Универсальная Сверхтехника: …»»): нужна живая речь.
    if {item.style for item in messages} & {"bare", "forum"}:
        talkative = sum(1 for item in messages if _CONVERSATIONAL_RE.search(item.message))
        if talkative * 3 < len(messages):
            return None
    if headers:
        return "chat"
    counts: dict[str, int] = {}
    for speaker in speakers:
        counts[speaker] = counts.get(speaker, 0) + 1
    if len(messages) >= 2 and max(counts.values()) >= 2:
        return "chat"
    if (
        len(messages) >= 2
        and participants
        and all(any(_names_match(speaker, known) for known in participants) for speaker in speakers)
    ):
        return "chat"
    return "weak" if len(messages) >= 2 else None


def is_chat(lines, participants=frozenset()) -> bool:
    return chat_verdict(lines, participants) == "chat"


def _names_match(speaker: str, reader: str) -> bool:
    """«Кен» и «Кен Амада» — один человек; «Дядя Ли» и «Дядя Ван» — нет."""
    name, reader = speaker.casefold(), " ".join(reader.split()).casefold()
    return bool(reader) and (name == reader or name.startswith(reader + " ") or reader.startswith(name + " "))


def chat_reader(windows_lines) -> str:
    """Кто читает чат: собеседник, который есть в большинстве переписок книги.

    «Кен» и «Кен Амада» считаются одним человеком. Нужны хотя бы две переписки
    с его участием, иначе справа никого нет.
    """
    presence: dict[str, int] = {}
    lines_count: dict[str, int] = {}
    for lines in windows_lines:
        speakers = {parsed.speaker for parsed in map(chat_line, lines) if parsed is not None}
        for speaker in speakers:
            presence[speaker] = presence.get(speaker, 0) + 1
        for parsed in map(chat_line, lines):
            if parsed is not None:
                lines_count[parsed.speaker] = lines_count.get(parsed.speaker, 0) + 1
    best, best_score = "", (0, 0)
    for name in sorted(presence, key=len):
        related = [other for other in presence if _names_match(other, name)]
        score = (sum(presence[other] for other in related), sum(lines_count.get(other, 0) for other in related))
        if score > best_score:
            best, best_score = name, score
    return best if best_score[0] >= 2 else ""


def chat_participants(windows_lines) -> frozenset:
    """Собеседники узнанных переписок: все, кто в них пишет."""
    return frozenset(
        parsed.speaker for lines in windows_lines for parsed in map(chat_line, lines) if parsed is not None
    )



# --- чей аккаунт в переписке ---------------------------------------------------

_PHONE = r"(?:телефон|мобильник|смартфон|мобильный)"
# «телефон Рена завибрировал», «на экране телефона Макото».
_PHONE_OF_RE = re.compile(rf"{_PHONE}\w*\s+([А-ЯЁA-Z][\w-]+)")
# «Макото достала телефон», «Кен вытащил свой телефон из сумки».
_OWN_PHONE_RE = re.compile(
    rf"([А-ЯЁA-Z][\w-]+)\s+(?:[а-яё]+\s+)?(?:достал|достала|вытащил|вытащила|разблокировал|разблокировала)\s+"
    rf"(?:свой\s+|свою\s+)?{_PHONE}"
)
# Кто пишет не из этого аккаунта: «сообщение от Анн», «написать Кену».
_SENDER_RE = re.compile(r"сообщени\w*\s+от\s+([А-ЯЁA-Z][\w-]+)", re.I)
_ADDRESSEE_RE = re.compile(r"(?:написать|написал|написала|ответить|ответил|ответила)\s+([А-ЯЁA-Z][\w-]+)")
_HEADER_FROM_RE = re.compile(r"^[\[【]?сообщени\w*\s+от\s*[:：]\s*(.+?)[.\]】]*$", re.I)
_HEADER_TO_RE = re.compile(r"^[\[【]?сообщени\w*\s+(?:для|отправлено)\s*[:：]\s*(.+?)[.\]】]*$", re.I)
_CHAT_CONTEXT_PARAGRAPHS = 3


def _name_form_of(word: str, name: str) -> bool:
    """«Рена», «Кену» — формы имени «Рен», «Кен»; по первому слову имени."""
    first = name.split()[0].lower() if name.split() else ""
    word = word.lower().strip(".,!?;:…»«\"'")
    if not first or not word:
        return False
    if word == first:
        return True
    base = first[:max(3, len(first) - 1)]
    return len(first) >= 3 and word.startswith(base) and 0 <= len(word) - len(first) <= 3


def _participant_named(word: str, speakers) -> str:
    return next((speaker for speaker in speakers if _name_form_of(word, speaker)), "")


def chat_account_owner(context, lines) -> str:
    """Чей аккаунт открыт в переписке: имя собеседника, чужое имя или пусто.

    Справа в чате стоят сообщения владельца телефона: того, под чьим
    аккаунтом читается переписка, а не того, кто держит телефон. Признаки —
    в последних абзацах перед чатом и в его шапке.
    """
    speakers: list[str] = []
    for parsed in map(chat_line, lines):
        if parsed is not None and parsed.speaker not in speakers:
            speakers.append(parsed.speaker)
    context = [text for text in context if text][-_CHAT_CONTEXT_PARAGRAPHS:]
    for text in reversed(context):
        for pattern in (_PHONE_OF_RE, _OWN_PHONE_RE):
            for match in pattern.finditer(text):
                word = match.group(1)
                named = _participant_named(word, speakers)
                if named:
                    return named
                if pattern is _PHONE_OF_RE:
                    # Телефон того, кто в переписке не пишет: справа никого.
                    return word
    # Не владелец: отправитель входящего и адресат исходящего сообщения.
    others: list[str] = []
    for line in lines:
        for pattern in (_HEADER_FROM_RE, _HEADER_TO_RE):
            match = pattern.match(line.strip())
            if match:
                others.append(match.group(1).split()[0])
    for text in context:
        others += [match.group(1) for match in _SENDER_RE.finditer(text)]
        others += [match.group(1) for match in _ADDRESSEE_RE.finditer(text)]
    if others:
        rest = [speaker for speaker in speakers if not any(_name_form_of(word, speaker) for word in others)]
        if len(rest) == 1 and len(speakers) >= 2:
            return rest[0]
    return ""


def window_readers(candidate, template) -> list[str]:
    """Чьи реплики окна чата справа: выбор на странице, владелец или читающий книги."""
    if candidate.readers is not None:
        return list(candidate.readers)
    if candidate.chat_owner:
        return [candidate.chat_owner]
    return chat_readers(template)



# --- целые элементы вокруг ветки форума -------------------------------------------

_TAG_TOKEN_RE = re.compile(r"<(/?)([a-zA-Z][\w:-]*)\b[^>]*?(/?)>")
_VOID_TAGS = frozenset("br hr img meta link input col wbr area base source track embed param".split())


def _unbalanced(raw: str, start: int, end: int):
    """Теги, закрытые в [start, end) без открытия, и открытые без закрытия."""
    stack: list[tuple[str, int]] = []
    closers: list[str] = []
    for tag in _TAG_TOKEN_RE.finditer(raw, start, end):
        name = tag.group(2).lower()
        if tag.group(3) or name in _VOID_TAGS:
            continue
        if tag.group(1):
            if stack and stack[-1][0] == name:
                stack.pop()
            else:
                closers.append(name)
        else:
            stack.append((name, tag.start()))
    return closers, stack


def _opening_before(raw: str, position: int, name: str) -> int | None:
    depth = 0
    tags = [tag for tag in _TAG_TOKEN_RE.finditer(raw, 0, position) if tag.group(2).lower() == name and not tag.group(3)]
    for tag in reversed(tags):
        if tag.group(1):
            depth += 1
        elif depth:
            depth -= 1
        else:
            return tag.start()
    return None


def _closing_after(raw: str, position: int, name: str) -> int | None:
    depth = 0
    for tag in _TAG_TOKEN_RE.finditer(raw, position):
        if tag.group(2).lower() != name or tag.group(3):
            continue
        if not tag.group(1):
            depth += 1
        elif depth:
            depth -= 1
        else:
            return tag.end()
    return None


def _balanced_span(raw: str, start: int, end: int) -> tuple[int, int] | None:
    """Расширить [start, end) до целых элементов: абзацы ветки в разных
    обёртках ``div`` (calibre) иначе оставили бы после замены висячие теги."""
    for _attempt in range(20):
        closers, openers = _unbalanced(raw, start, end)
        if not closers and not openers:
            return start, end
        for name in closers:
            found = _opening_before(raw, start, name)
            if found is None or name in ("body", "html"):
                return None
            start = found
        for name, _position in reversed(openers):
            found = _closing_after(raw, end, name)
            if found is None or name in ("body", "html"):
                return None
            end = found
    return None


_FOREIGN_TAG_RE = re.compile(r"<(?:img|image|svg|object|video|audio)\b", re.I)


def _foreign_content(raw: str, span: tuple[int, int], chosen) -> bool:
    """В участке, кроме этих абзацев, есть текст или картинка.

    Пустые обёртки, линейки и цитаты без своего текста не в счёт: в рамке они
    пропадут, при снятии оформления вернутся.
    """
    rest, position = [], span[0]
    for paragraph in chosen:
        rest.append(raw[position:paragraph.start])
        position = paragraph.end
    rest.append(raw[position:span[1]])
    rest = _COMMENT_RE.sub("", "".join(rest))
    return bool(_FOREIGN_TAG_RE.search(rest) or html_module.unescape(_TAG_RE.sub("", rest)).strip())


def _clean_span(raw: str, paragraphs, first: int, stop: int) -> tuple[int, int] | None:
    """Участок замены абзацев [first, stop) или ``None``, если он проглотил бы чужое."""
    start, end = paragraphs[first].start, paragraphs[stop - 1].end
    if not any(paragraphs[number].wrapped for number in range(first + 1, stop)):
        return start, end
    span = _balanced_span(raw, start, end)
    if span is None or _foreign_content(raw, span, paragraphs[first:stop]):
        return None
    return span


def _span_pieces(raw: str, chosen, start: int, end: int) -> list[str]:
    """``paragraph_html`` кандидата: сами абзацы или весь участок вместе с обёртками."""
    if start == chosen[0].start and end == chosen[-1].end:
        return [raw[paragraph.start:paragraph.end] for paragraph in chosen]
    return [raw[start:end]]


def _window_pieces(raw: str, paragraphs, first: int, stop: int) -> list[tuple[int, int, int, int]]:
    """Серию абзацев [first, stop) — на куски, которые заменяются целыми элементами.

    Абзацы-братья заменяются как есть. Абзацы в своих обёртках (страницы
    веб-новелл) заменяются вместе с обёртками. Если в обёртке есть что-то
    чужое (проза перед веткой форума в той же ``div``, заголовок главы),
    серия режется на границе обёртки: иначе замена проглотила бы и его.
    Возвращает [(первый, конец, начало участка, конец участка)].
    """
    pieces = []
    while first < stop:
        cuts = [number for number in range(first + 1, stop) if paragraphs[number].wrapped]
        for end in [stop, *reversed(cuts)]:
            span = _clean_span(raw, paragraphs, first, end)
            if span is not None:
                pieces.append((first, end, *span))
                first = end
                break
    return pieces


# --- соглашения книги о скобках ----------------------------------------------------

# Слова системы: по ним строку в скобках оставляют окном и в книгах, где скобки
# означают речь или заголовки сцен.
_SYSTEM_WORD_RE = re.compile(
    r"динь|дзынь|систем|навык|уровень|уровня|статус|задани|квест|наград|очк[иоа]|опыт|получен|поздравля|"
    r"тревог|вниман|обнаружен|предупрежд|ошибк|данные|характеристик|способност|умени|титул|достижени|"
    r"\bhp\b|\bmp\b|\bexp\b",
    re.I,
)
_FIRST_PERSON_BRACKET_RE = re.compile(
    r"(?<![\w-])(?:я|мне|меня|мной|мой|моя|моё|мое|мои|моей|моего|моим|мы|нас|нам|нами|наш|наша|наше|наши)(?![\w-])",
    re.I,
)
_REMARK_AFTER_BRACKET_RE = re.compile(r"[\]】]\s*[–—-]\s*\S")
_RAW_PARAGRAPH_RE = re.compile(r"<p\b[^>]*>(.*?)</p\s*>", re.I | re.S)
_CHAPTER_HEADERS_SHARE = 0.3
_SPEECH_BRACKETS_SHARE = 0.4
_BOOK_MIN_BRACKET_LINES = 20
_BOOK_MIN_CHAPTERS = 5
_HEADER_LINES_MAX = 4


def _raw_paragraph_texts(html: str) -> list[str]:
    """Быстрый текст абзацев для статистики книги (без разбора bs4)."""
    return [" ".join(html_module.unescape(_TAG_RE.sub(" ", body)).split()) for body in _RAW_PARAGRAPH_RE.findall(html)]


def _bracketed(text: str) -> bool:
    """Строка в скобках, но не реплика чата «[Кен]: …» — у той свои правила."""
    if not text.lstrip("—–- ").startswith(("[", "【", "〖")):
        return False
    parsed = chat_line(text)
    return parsed is None or parsed.style != "bracket"


def book_bracket_conventions(chapters_html) -> dict:
    """Что значат квадратные скобки в книге.

    ``chapter_headers``: треть глав и больше открывается строкой в скобках —
    заголовки сцен (The Shard Shrouded). ``speech_brackets``: в скобках чаще
    пишут от первого лица, чем бывает у системы, — мысленная речь (The Limits
    of Power). Системных книг ни то, ни другое не касается: там от первого
    лица меньше десятой части скобочных строк, а главы скобкой открываются редко.
    """
    chapters = opened = lines = first_person = system = 0
    for html in chapters_html:
        texts = [text for text in _raw_paragraph_texts(html) if text]
        if not texts:
            continue
        chapters += 1
        opened += _bracketed(texts[0]) and not _SYSTEM_WORD_RE.search(texts[0])
        for text in texts:
            if not _bracketed(text):
                continue
            lines += 1
            first_person += bool(_FIRST_PERSON_BRACKET_RE.search(text) or _REMARK_AFTER_BRACKET_RE.search(text))
            system += bool(_SYSTEM_WORD_RE.search(text))
    return {
        "chapter_headers": chapters >= _BOOK_MIN_CHAPTERS and opened >= _CHAPTER_HEADERS_SHARE * chapters,
        "speech_brackets": (
            lines >= _BOOK_MIN_BRACKET_LINES
            and first_person >= _SPEECH_BRACKETS_SHARE * lines
            and system < first_person
        ),
    }


# Разрыв сцены: пустой абзац или одни разделители («— ​», «***», «■»).
_SCENE_BREAK_RE = re.compile(r"^[\s\u200b\u00a0—–\-*•·■□◆◇~=_#]*$")
_HEADER_MAX = 200


def _convention_skips(paragraphs, settings) -> set[int]:
    """Номера абзацев, которые соглашения книги о скобках исключают из окон."""
    skipped: set[int] = set()
    if settings.chapter_headers:
        # Заголовок сцены: в начале главы или сразу после разрыва сцены
        # («[Эми Даллон 03]», «[Странное измерение, на пляже?]»). Сообщение системы
        # там же узнаётся по системному слову с «:» или «!» («[Тревога! Сеть атакована!]»);
        # строка посреди сцены («[Он видит вас. Немедленно отступайте!]») — окно, как и было.
        starting = True
        for number, paragraph in enumerate(paragraphs):
            text = paragraph.text
            if _SCENE_BREAK_RE.match(text):
                starting = True
                continue
            if (
                starting
                and _bracketed(text)
                and len(text) <= _HEADER_MAX
                and not (_SYSTEM_WORD_RE.search(text) and re.search(r"[:!]", text))
            ):
                skipped.add(number)
                continue
            starting = False
    if settings.speech_brackets:
        # Мысленная речь в скобках: от первого лица, с ремаркой или без слов системы.
        skipped.update(
            number for number, paragraph in enumerate(paragraphs)
            if _bracketed(paragraph.text) and (
                not _SYSTEM_WORD_RE.search(paragraph.text)
                or _FIRST_PERSON_BRACKET_RE.search(paragraph.text)
                or _REMARK_AFTER_BRACKET_RE.search(paragraph.text)
            )
        )
    return skipped


def find_windows(
    html: str,
    settings: DetectorSettings | None = None,
    source_html: str | None = None,
    source_marks: set[int] | None = None,
    weak_chats: list | None = None,
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

    chat_ends: dict[int, int | None] = {}

    def chat_run_end(position: int) -> int | None:
        """Конец переписки, которая начинается с ``position``, или ``None``."""
        if position in chat_ends:
            return chat_ends[position]
        stop = position
        while stop < len(paragraphs) and (stop == position or paragraphs[stop].adjacent):
            paragraph = paragraphs[stop]
            if excluded(paragraph) or (chat_line(paragraph.text) is None and not is_chat_header(paragraph.text)):
                break
            stop += 1
        # Шапка без реплик после неё — не переписка.
        while stop > position and is_chat_header(paragraphs[stop - 1].text):
            stop -= 1
        lines = [paragraph.text for paragraph in paragraphs[position:stop]]
        verdict = chat_verdict(lines, settings.chat_participants) if stop > position else None
        if verdict == "weak" and weak_chats is not None and (position == 0 or not paragraphs[position].adjacent
                                                               or chat_line(paragraphs[position - 1].text) is None):
            weak_chats.append(lines)
        chat_ends[position] = stop if verdict == "chat" else None
        return chat_ends[position]

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
    convention_skips = _convention_skips(paragraphs, settings)
    forum_at: dict[int, tuple[int, WindowCandidate]] = {}
    texts = [paragraph.text for paragraph in paragraphs]
    if any(forum_role(text) in ("welcome", "topic", "pm") for text in texts):
        position = 0
        while position < len(paragraphs):
            # Пустые абзацы и «P.S.» внутри поста — часть ветки, их не исключаем.
            stop = forum_run_end(texts, position) if paragraphs[position].text else None
            if stop is None:
                position += 1
                continue
            for first, end, span_start, span_end in _window_pieces(html, paragraphs, position, stop):
                lines = [paragraphs[number].text for number in range(first, end)]
                if not any(lines):
                    continue
                forum_at[first] = (
                    end,
                    WindowCandidate(
                        start=span_start,
                        end=span_end,
                        kind="forum",
                        lines=lines,
                        paragraph_html=[html[span_start:span_end]],
                        origin="forum",
                    ),
                )
            position = stop
    index = 0
    while index < len(paragraphs):
        first = paragraphs[index]
        text = first.text
        if index in forum_at:
            index, forum_window = forum_at[index]
            windows.append(forum_window)
            continue
        if excluded(first) or index in convention_skips:
            index += 1
            continue

        chat_stop = chat_run_end(index)
        if chat_stop is not None:
            context = [paragraph.text for paragraph in paragraphs[max(0, index - _CHAT_CONTEXT_PARAGRAPHS):index]]
            for first, end, span_start, span_end in _window_pieces(html, paragraphs, index, chat_stop):
                lines = [paragraph.text for paragraph in paragraphs[first:end]]
                windows.append(
                    WindowCandidate(
                        start=span_start,
                        end=span_end,
                        kind="chat",
                        lines=lines,
                        paragraph_html=_span_pieces(html, paragraphs[first:end], span_start, span_end),
                        origin="chat",
                        chat_owner=chat_account_owner(context, lines),
                    )
                )
            index = chat_stop
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
            if chat_run_end(stop) is not None or stop in forum_at or stop in convention_skips:
                break
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

        origin = _origin(shape, is_marked, text)
        pieces = _window_pieces(html, paragraphs, index, stop)
        for first, end, span_start, span_end in pieces:
            chosen = paragraphs[first:end]
            lines = [paragraph.text for paragraph in chosen]
            # Строка, отрезанная границей обёртки, — окно, только если она и сама
            # по себе системная (не шапка «Статус:» без данных под ней).
            if len(pieces) > 1 and len(lines) == 1 and not (_is_data_line(lines[0]) or confirmed(first)):
                continue
            windows.append(
                WindowCandidate(
                    start=span_start,
                    end=span_end,
                    kind=classify_kind(lines),
                    lines=lines,
                    paragraph_html=_span_pieces(html, chosen, span_start, span_end),
                    origin=origin,
                )
            )
        index = stop
    _carry_chat_owners(windows)
    return windows


def _carry_chat_owners(windows) -> None:
    """Следующие чаты главы — из того же аккаунта, если его владелец в них пишет.

    Перед вторым и третьим чатом сцены о телефоне обычно уже не говорят.
    """
    owner = ""
    for window in windows:
        if window.kind != "chat":
            continue
        if window.chat_owner:
            owner = window.chat_owner
            continue
        if owner:
            speakers = [parsed.speaker for parsed in map(chat_line, window.lines) if parsed is not None]
            same = next((speaker for speaker in speakers if _names_match(speaker, owner) or _name_form_of(owner, speaker)), "")
            if same:
                window.chat_owner = same


# --- оформление -------------------------------------------------------------

DEFAULT_TEMPLATES = {
    "status": {
        "label": "Статус", "border": "#4fc3f7", "background": "#0b1622", "text": "#e6edf5",
        "accent": "#7fd3ff", "icon": "◆", "upper": True, "columns": 1,
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
    # Переписка: реплики собеседников слева, того, кто читает чат, — справа.
    # Кто читает, передаётся в шаблоне ключом ``readers`` (строка через запятую или список).
    "chat": {
        "label": "Чат", "border": "#4db6ac", "background": "#0e1716", "text": "#e6f2f0",
        "accent": "#80cbc4", "icon": "", "upper": False, "columns": 1,
    },
    # Ветка форума (ПЛО в «Черве»): тема, посты карточками, страницы.
    "forum": {
        "label": "Форум", "border": "#5c6bc0", "background": "#10131c", "text": "#e3e7f1",
        "accent": "#9fa8da", "icon": "", "upper": False, "columns": 1,
    },
}
KIND_ORDER = ("status", "skill", "notice", "levelup", "achievement", "chat", "forum")

_TITLE_KEYS = (
    "навык", "способност", "умени", "заклинани", "достижени", "статус",
    "уведомлени", "система", "квест", "задание", "предмет",
)
_NBSP = "\u00a0"
_SEPARATOR = f"{_NBSP}|{_NBSP}"
_SHORT_VALUE = 25
_ITALIC_FROM = 40


# Разряды числа «2 618 757»: на узкой колонке перенос посреди числа.
_DIGIT_GROUP_RE = re.compile(r"(?<=\d) (?=\d{3}(?!\d))")


def _escape(text: str) -> str:
    return html_module.escape(_DIGIT_GROUP_RE.sub(_NBSP, text), quote=False)


_TITLE_MAX_CHARS = 60


def _looks_like_title(text: str) -> bool:
    if len(text) > _TITLE_MAX_CHARS or bracket_shape(text) in ("keyed", "list") or is_bullet_line(text):
        return False
    if not is_key_value(text):
        return True
    key = re.split(r"[:：]", text, maxsplit=1)[0].strip().lower()
    # «2 предмета: …» — бонус комплекта, а не карточка «Предмет: Меч».
    if key[:1].isdigit() or _SET_BONUS_KEY_RE.match(key):
        return False
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


def _drop_field_period(text: str) -> str:
    """«Качество: Легендарное.» → без точки: в столбике полей она лишняя."""
    if not text.endswith(".") or text.endswith(".."):
        return text
    bare = text[:-1]
    if _STAT_DELTA_FIELD_RE.match(bare) and _KV_RE.match(bare) is None:
        return bare
    if _KV_RE.match(bare) and len(re.split(r"[:：]", bare, maxsplit=1)[1].strip()) <= _SHORT_VALUE:
        return bare
    return text


_LIST_GROUP_RE = re.compile(r"[\[【〖]([^\[\]【】〖〗]*)[\]】〗]")
_LIST_TAIL_RE = re.compile(r"[\]】〗]([^\]】〗]*)$")
_LONG_KEY_RE = re.compile(r"^([^:：«»\"“”.!?…]{1,40})[:：]\s+(\S.*)$", re.S)
_CARD_KEY_WORDS = 4


def _render_group_list(text: str, accent: str) -> str:
    """«[А ур. 1] [Б ур. 8]» → «А ур. 1 · Б ур. 8»: без запятых пункты слиплись бы.

    Перечень через запятую («[Тошнота], [Отравление].») остаётся как написан.
    """
    if re.search(r"[\]】〗]\s*[,;]", text):
        return _escape(strip_brackets(text))
    items = [item.strip() for item in _LIST_GROUP_RE.findall(text) if item.strip()]
    tail = _LIST_TAIL_RE.search(text.strip())
    parts = [_render_key_value_part(item, accent) if is_key_value(item) else _escape(item) for item in items]
    return " · ".join(parts) + (_escape(tail.group(1).strip()) if tail else "")


def _render_row(text: str, accent: str, italic_allowed: bool, card: bool = False):
    """Строки окна: [(html, короткая ли это пара ключ-значение для колонок)].

    ``card`` — в окне несколько пар «ключ: значение»: ключ выделяется и у
    строки с длинным значением, иначе одни дни симулятора жирные, другие нет.
    """
    shape = bracket_shape(text)
    if shape == "keyed":
        return [(_render_keyed(text, accent), False)]
    if shape == "list":
        return [(_render_group_list(text, accent), False)]
    if is_section_label(text):
        label = _BULLET_MARK_RE.sub("", text.strip(), count=1)
        label = re.sub(r"^[—–-]\s*", "", label).rstrip(":：…").strip()
        # «Пламенная Душа» (пассивный навык) → Пламенная Душа (пассивный навык)
        label = re.sub(r"^«([^«»]+)»", r"\1", label).rstrip(".").strip()
        return [(f'<b style="color:{accent};">{_escape(label)}:</b>', False)]
    text = _drop_field_period(_strip_outer_quotes(strip_brackets(text)))
    # «<[Месть]: Если…>»: термин в скобках внутри внешних скобок.
    if shape == "full" and bracket_shape(text) == "keyed":
        return [(_render_keyed(text, accent), False)]
    delta = _STAT_DELTA_FIELD_RE.match(text)
    if delta and _KV_RE.match(text) is None:
        name, amount = delta.group(1).strip(), delta.group(2).strip()
        return [(f'<b style="color:{accent};">{_escape(name)}</b> {_escape(amount)}', False)]
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
    long_key = _LONG_KEY_RE.match(text) if card else None
    if long_key and len(long_key.group(1).split()) <= _CARD_KEY_WORDS:
        key, value = long_key.group(1).strip().strip(_KEY_QUOTES), long_key.group(2).strip()
        return [(f'<b style="color:{accent};">{_escape(key)}:</b> {_escape(value)}', False)]
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


# --- карточка одним абзацем ---------------------------------------------------

# Предмет или монстр одним абзацем, как в «Щите небосвода»: «[Меч]: Уровень: 38,
# Качество: Легендарное. Атака: 180~220. Сила +60. …». В рамке такие поля
# идут по одному в строке.
_CARD_FIELDS_MIN = 3
_CARD_FIELD_VALUE_MAX = 60
_CARD_LABEL_WORDS_MAX = 3
# Характеристика со знаком: «Сила +60», «Опыт +100 000», «Скорость -10%».
_STAT_DELTA_FIELD_RE = re.compile(
    r"^([^\W\d_][^:：+＋\-−–!?.…]{0,40}?)\s([+＋\-−–]\s?\d[\d\s]*(?:[.,]\d+)?\s?%?)$"
)
# Сокращения, после точки которых предложение не кончается.
_ABBREVIATIONS = frozenset("ед ур сек мин ч шт им г гг т тд тп др см стр св кг км м ок".split())
_PAIRS = {"«": "»", "(": ")", "[": "]", "【": "】", "〖": "〗"}


def _split_top_level(text: str, boundary) -> list[str]:
    """Разрезать текст по границам вне кавычек и скобок.

    ``boundary(text, index)`` решает, кончается ли кусок на символе ``index``.
    """
    pieces, stack, start = [], [], 0
    for index, char in enumerate(text):
        if stack and char == stack[-1]:
            stack.pop()
        elif char in _PAIRS:
            stack.append(_PAIRS[char])
        elif not stack and boundary(text, index):
            pieces.append(text[start:index + 1].strip())
            start = index + 1
    pieces.append(text[start:].strip())
    return [piece for piece in pieces if piece]


def _sentence_end(text: str, index: int) -> bool:
    if text[index] != "." or index + 2 >= len(text) or not text[index + 1].isspace():
        return False
    following = text[index + 2:].lstrip()[:1]
    if not following or not (following.isupper() or following.isdigit() or following in "«(["):
        return False
    word = re.search(r"([^\W\d_]+)$", text[:index])
    return not (word and word.group(1).lower() in _ABBREVIATIONS)


def _comma(text: str, index: int) -> bool:
    return text[index] == "," and index + 1 < len(text) and text[index + 1].isspace()


def _is_card_field(piece: str) -> bool:
    piece = piece.strip().rstrip(".")
    if _STAT_DELTA_FIELD_RE.match(piece) and _KV_RE.match(piece) is None:
        return True
    if _KV_RE.match(piece) is None:
        return False
    key, value = re.split(r"[:：]", piece, maxsplit=1)
    key, value = key.strip().strip(_KEY_QUOTES), value.strip()
    return bool(value) and len(key.split()) <= 4 and len(value) <= _CARD_FIELD_VALUE_MAX


def _is_card_label(piece: str) -> bool:
    """Короткая пометка перед полем: «Одноручный меч, Атака: 180~220»."""
    return (
        len(piece.split()) <= _CARD_LABEL_WORDS_MAX
        and _KV_RE.match(piece) is None
        and not any(char.isdigit() for char in piece)
    )


def _card_pieces(text: str) -> list[tuple[str, bool]]:
    """Поля карточки по одному: ``[(строка, это ли название карточки)]``.

    Абзац без трёх коротких полей остаётся одной строкой: сообщение прозой
    («Игрок принял задание … Пожалуйста, выберите сложность.») не режем.
    """
    stripped = text.strip()
    # Бонус комплекта «4 вещи: … . … .» — одно поле со списком эффектов.
    if _KV_RE.match(stripped) and _SET_BONUS_KEY_RE.match(re.split(r"[:：]", stripped, maxsplit=1)[0].strip()):
        return [(text, False)]
    head, body = None, stripped
    shape = bracket_shape(stripped)
    if shape == "keyed":
        match = _KEYED_RE.match(stripped)
        rest = match.group(2).strip()
        if rest.startswith((":", "：")):
            head, body = f"[{match.group(1).strip()}]", rest[1:].strip()
    elif shape == "full":
        body = strip_brackets(stripped)
    pieces: list[str] = []
    for sentence in _split_top_level(body, _sentence_end):
        parts = _split_top_level(sentence, _comma)
        parts = [part.rstrip(",").strip() for part in parts]
        if len(parts) > 1 and all(
            _is_card_field(part) or (position == 0 and _is_card_label(part))
            for position, part in enumerate(parts)
        ):
            pieces.extend(parts)
        else:
            pieces.append(sentence)
    fields = sum(1 for piece in pieces if _is_card_field(piece))
    if fields < _CARD_FIELDS_MIN or fields * 2 < len(pieces):
        return [(text, False)]
    return ([(head, True)] if head else []) + [(piece, False) for piece in pieces]


# --- переписка: пузыри --------------------------------------------------------

# Пузыри — плавающие ``span``: вложенный ``div`` сломал бы разбор блока до
# первого ``</div>``, а ``display`` Rulate вырезает. ``float`` и ``clear``
# очистка HTML на сайте пропускает.
_CHAT_CLEAR = '<br style="clear:both;" />'
_CHAT_SIDE_MARGIN = "22%"


def _mix(first: str, second: str, share: float) -> str:
    """Цвет между двумя #rrggbb: доля ``share`` второго."""
    try:
        a = [int(first[i:i + 2], 16) for i in (1, 3, 5)]
        b = [int(second[i:i + 2], 16) for i in (1, 3, 5)]
    except (TypeError, ValueError):
        return first
    return "#" + "".join(f"{round(x + (y - x) * share):02x}" for x, y in zip(a, b))


def chat_bubble_colors(template) -> tuple[str, str]:
    """Фон пузырей: (собеседники, тот, кто читает) — из цветов шаблона чата."""
    return (
        _mix(template["background"], template["text"], 0.12),
        _mix(template["background"], template["accent"], 0.32),
    )


def chat_readers(template) -> list[str]:
    value = (template or {}).get("readers") or []
    if isinstance(value, str):
        value = value.split(",")
    return [" ".join(str(item).split()) for item in value if str(item).strip()]


def is_reader(speaker: str, readers) -> bool:
    return any(_names_match(speaker, reader) for reader in readers)


def _original_attr(source_html) -> str:
    if not source_html:
        return ""
    if not isinstance(source_html, str):
        source_html = "".join(source_html)
    # Переносы строк исходника прячем в сущности: блок обязан остаться одной
    # строкой, а html.unescape вернёт их при снятии оформления.
    original = html_module.escape(source_html, quote=True).replace("\r", "&#13;").replace("\n", "&#10;")
    return f' {BLOCK_ATTR}-orig="{original}"'


def _render_chat(texts, template, source_html) -> str:
    accent, text_color = template["accent"], template["text"]
    background, border = template["background"], template["border"]
    other_background, own_background = chat_bubble_colors(template)
    readers = chat_readers(template)
    pieces: list[str] = []
    previous = None
    floating = False
    for text in texts:
        parsed = chat_line(text)
        if parsed is None:
            if floating:
                pieces.append(_CHAT_CLEAR)
                floating = False
            caption = strip_brackets(text) if bracket_shape(text) in ("full", "keyed") else text
            pieces.append(f'<span style="color:{accent};font-size:0.9em;">{_escape(caption)}</span>{_CHAT_CLEAR}')
            previous = None
            continue
        own = is_reader(parsed.speaker, readers)
        name = ""
        if not own and parsed.speaker != previous:
            name = f'<b style="color:{accent};font-size:0.85em;">{_escape(parsed.speaker)}</b><br />'
        margin = f"3px 0 3px {_CHAT_SIDE_MARGIN}" if own else f"3px {_CHAT_SIDE_MARGIN} 3px 0"
        style = (
            f"float:{'right' if own else 'left'};clear:both;margin:{margin};padding:5px 10px;"
            f"background:{own_background if own else other_background};border-radius:10px;text-align:left;"
        )
        pieces.append(f'<span style="{style}">{name}{_escape(parsed.message)}</span>')
        previous = parsed.speaker
        floating = True
    if floating:
        pieces.append(_CHAT_CLEAR)
    style = (
        f"margin:16px 0;padding:10px 12px;border:2px solid {border};border-left:8px solid {border};"
        f"background:{background};color:{text_color};text-align:center;line-height:1.45;"
    )
    block = f'<div {BLOCK_ATTR}="chat"{_original_attr(source_html)} style="{style}">' + "".join(pieces) + "</div>"
    return re.sub(r"\s*\n\s*", " ", block)



# --- форум: ПЛО в фанфиках по «Червю» -----------------------------------------

# Остатки BB-кода из исходника («Mama Snek»): «[b]Баграт [/b]», «[indent]».
_BBCODE_RE = re.compile(
    r"\[/?(?:b|i|u|s|indent|center|right|left|quote|url|img|color|size|spoiler|sub|sup|font)(?:=[^\]]*)?\]",
    re.I,
)
_ZERO_WIDTH_RE = re.compile("[​‌‍﻿]")
_FORUM_WELCOME_RE = re.compile(
    r"^(?:добро пожаловать на (?:форум|доск)|вы\s+(?:сейчас\s+)?(?:вошли|авторизован\w*|залогинен\w*)|вы просматриваете"
    r"|welcome to the parahumans|you are currently logged in|you are viewing)",
    re.I,
)
# Шапка «Вы просматриваете:» — с маркерами «•» или без них, как в «Калико».
_FORUM_INFO_RE = re.compile(
    r"^(?:[•·]\s*\S|(?:(?:и|или|and|or)\s+)?(?:темы\b|личные сообщения|отображается|десять\s+(?:постов|сообщений)"
    r"|последние\s+десять|threads\b|private messages|thread op|ten posts|last ten)"
    r"|у вас \d+ (?:нарушени|предупреждени)|you have \d+ (?:infraction|warning))",
    re.I,
)
_FORUM_DECOR_RE = re.compile(r"^(?:■|□|\[\s*[–—-]\s*\]|…|\.{3})$")
_FORUM_TOPIC_RE = re.compile(r"^[♦◆]?\s*(?:тема|topic)\s*[:：]\s*(\S.*)$", re.I)
_FORUM_LIST_RE = re.compile(r"^(новости|фанфики|объявлени\w*|news|fanfiction)\s*[:：]\s*(\S.*)$", re.I)
_FORUM_BOARD_RE = re.compile(r"^(?:в разделе|раздел|в|in)\s*[:：]\s*(\S.*)$", re.I)
_FORUM_TIME_RE = re.compile(r"^(?:опубликова\w*|ответил\w*|ответ от|отправлено|posted|replied)\b", re.I)
_FORUM_PAGE_RE = re.compile(r"^\(?\s*(?:показ\w*\s+страниц\w*|showing page)\b", re.I)
_FORUM_END_RE = re.compile(r"^(?:конец страницы|end of page)\b", re.I)
# Шапка личного сообщения: «♦ Личное сообщение от …», «Новое сообщение для …»; фраза
# «Новых сообщений так и не появилось» — проза.
_FORUM_PM_RE = re.compile(
    r"^(?:[♦◆]\s*(?:(?:личн\w+|нов\w+)\s+сообщени\w*|private message)"
    r"|(?:(?:личн\w+|нов\w+)\s+сообщени[ея]|private message)\s+(?:от|для|from|to)\b)",
    re.I,
)
# Конец поста и начало повествования: реплика с тире или разрыв сцены.
_FORUM_STOP_RE = re.compile(r"^(?:[—–]\s|\*\s*\*\s*\*|\*{3,}|[—–]\s*\+|~{3,})")
_FORUM_AUTHOR_RE = re.compile(
    r"^(?:[…☐□■►▶◆♦•·]\s*)*(?P<name>[^\s()\[\]►▶…☐□■◆♦•·][^()\[\]]{0,40}?)\s*"
    r"(?P<tags>(?:[(\[][^()\[\]]{1,40}[)\]]\s*)*)\.?$"
)
_FORUM_AUTHOR_MAX = 90
# Сколько строк тела допустимо между заголовками постов и после последнего.
_FORUM_GAP_AFTER = {"time": 25, "end": 2}
_FORUM_GAP_DEFAULT = 6
_FORUM_TAIL = 8
# Глава-интерлюдия целиком из форума: ветка начинается в первых абзацах.
_FORUM_INTERLUDE_START = 3
# Столько пустых абзацев подряд — конец раздела (дальше послесловие автора).
_FORUM_EMPTY_RUN = 3


def forum_clean(text: str) -> str:
    return " ".join(_ZERO_WIDTH_RE.sub("", _BBCODE_RE.sub("", text)).split())


_LEADING_ELLIPSIS_RE = re.compile(r"^(?:…|\.\.\.)\s*(?=\S)")


def _forum_key(text: str) -> str:
    """Строка для узнавания роли: без «…» в начале («…Конец страницы. 1, 2»)."""
    return _LEADING_ELLIPSIS_RE.sub("", forum_clean(text))


def forum_role(text: str, following: str = "") -> str:
    """Роль строки ветки форума: topic, board, author, time, page, end, pm, list,
    welcome, info, decor или body."""
    line = _forum_key(text)
    if not line:
        return "empty"
    if _FORUM_WELCOME_RE.match(line):
        return "welcome"
    if _FORUM_TOPIC_RE.match(line):
        return "topic"
    if _FORUM_PM_RE.match(line) and len(line) <= 120:
        return "pm"
    if _FORUM_LIST_RE.match(line):
        return "list"
    if _FORUM_BOARD_RE.match(line) and len(line) <= 140:
        return "board"
    if _FORUM_TIME_RE.match(line) and len(line) <= 80:
        return "time"
    if _FORUM_PAGE_RE.match(line) and len(line) <= 60:
        return "page"
    if _FORUM_END_RE.match(line) and len(line) <= 200:
        return "end"
    if _FORUM_DECOR_RE.match(line):
        return "decor"
    if _FORUM_INFO_RE.match(line) and len(line) <= 140:
        return "info"
    if (
        following
        and _FORUM_TIME_RE.match(_forum_key(following))
        and len(line) <= _FORUM_AUTHOR_MAX
        and _FORUM_AUTHOR_RE.match(line)
    ):
        return "author"
    return "body"


def _forum_roles(texts) -> list[str]:
    return [forum_role(text, texts[index + 1] if index + 1 < len(texts) else "") for index, text in enumerate(texts)]


# Ремарка после реплики: «…, — сказала она», «…? — ответила я».
_SPEECH_REMARK_RE = re.compile(r"[,!?…]\s*[—–]\s*[а-яёa-z]")


def _post_list_continues(window, roles, position: int, last: int, limit: int) -> bool:
    """Строки с тире — пункты поста («— В «Ящике…» есть участница…»), а не реплика
    после ветки: без ремарок, и в пределах разрыва за ними снова идут посты."""
    following = position
    while following < len(roles) and roles[following] in ("body", "empty"):
        if _SPEECH_REMARK_RE.search(window[following]):
            return False
        following += 1
    return following < len(roles) and roles[following] != "decor" and following - last <= limit


def forum_run_end(texts, index: int) -> int | None:
    """Конец ветки форума, которая начинается со строки ``index``, или ``None``.

    Ветка начинается шапкой («Добро пожаловать на форумы…»), темой или личным
    сообщением. Тело поста тянется до следующего заголовка поста, но не дальше
    25 строк; после «Конец страницы» ветку продолжает только следующая страница
    сразу за ней. Без «Конца страницы» у последнего поста берутся до 8 строк
    тела (в главе-интерлюдии — до конца главы), и реплика с тире или разрыв
    сцены его обрывают.
    """
    window = texts[index:]
    roles = _forum_roles(window)
    if not roles or roles[0] not in ("welcome", "topic", "pm"):
        return None
    last, posts, lists, messages = 0, 0, 0, 0
    empties = 0
    # Роль, по которой меряется допустимый разрыв: разделитель «■» после
    # «Конца страницы» ветку не продолжает, а после поста — не обрывает.
    anchor_role = roles[0]
    position = 1
    while position < len(roles):
        role = roles[position]
        empties = empties + 1 if role == "empty" else 0
        if empties >= _FORUM_EMPTY_RUN:
            break
        if role in ("body", "empty"):
            limit = _FORUM_GAP_AFTER.get(anchor_role, _FORUM_GAP_DEFAULT)
            line = forum_clean(window[position])
            if _FORUM_STOP_RE.match(line) and not (
                line.startswith(("—", "–")) and _post_list_continues(window, roles, position, last, limit)
            ):
                break
            if position - last > limit:
                break
            position += 1
            continue
        if anchor_role == "end" and position - last > _FORUM_GAP_AFTER["end"]:
            break
        if role == "time":
            posts += 1
        elif role in ("list", "topic"):
            lists += 1
        elif role == "pm":
            messages += 1
        last = position
        if role != "decor":
            anchor_role = role
        position += 1
    if not posts and lists < 2 and not messages and roles[0] != "pm":
        return None
    stop = last + 1
    if anchor_role != "end":
        tail_limit = len(roles) if index < _FORUM_INTERLUDE_START else _FORUM_TAIL
        blank = 0
        while (
            stop < len(roles)
            and stop - last <= tail_limit
            and roles[stop] in ("body", "empty", "decor")
            and not _FORUM_STOP_RE.match(forum_clean(window[stop]))
        ):
            blank = blank + 1 if roles[stop] == "empty" else 0
            if blank >= 2:
                stop -= 1
                break
            stop += 1
        # Пустые абзацы в конце ветки оставляем тексту главы.
        while stop > last + 1 and roles[stop - 1] == "empty":
            stop -= 1
    return index + stop


def forum_structure(lines):
    """Ветка по частям: [("welcome", [строки]), ("topic", тема, раздел),
    ("post", ник, [метки], время, [строки тела]), ("pm", заголовок, [тело]),
    ("list", метка, текст), ("page", текст), ("decor", текст)]."""
    texts = [forum_clean(line) for line in lines]
    roles = _forum_roles(texts)
    parts: list[tuple] = []
    current = None
    for body_text, role in zip(texts, roles):
        if not body_text:
            continue
        # Служебные строки — без «…» в начале, тело поста — как написано.
        text = body_text if role == "body" else _forum_key(body_text)
        if role in ("welcome", "info"):
            if parts and parts[-1][0] == "welcome":
                parts[-1][1].append(text)
            else:
                parts.append(("welcome", [text]))
            current = None
        elif role == "topic":
            parts.append(("topic", _FORUM_TOPIC_RE.match(text).group(1).strip(), ""))
            current = None
        elif role == "board":
            board = _FORUM_BOARD_RE.match(text).group(1).strip()
            if parts and parts[-1][0] == "topic" and not parts[-1][2]:
                parts[-1] = ("topic", parts[-1][1], board)
            else:
                parts.append(("page", text))
            current = None
        elif role == "pm":
            current = ["pm", text.lstrip("♦◆ ").rstrip(":："), []]
            parts.append(current)
        elif role == "list":
            match = _FORUM_LIST_RE.match(text)
            parts.append(("list", match.group(1), match.group(2)))
            current = None
        elif role == "author":
            match = _FORUM_AUTHOR_RE.match(text)
            tags = [tag.strip("()[] ") for tag in re.findall(r"[(\[][^()\[\]]+[)\]]", match.group("tags"))]
            current = ["post", match.group("name").strip().rstrip("."), tags, "", []]
            parts.append(current)
        elif role == "time":
            if current is not None and current[0] == "post" and not current[3]:
                current[3] = text
            else:
                current = ["post", "", [], text, []]
                parts.append(current)
        elif role in ("page", "end"):
            parts.append(("page", text))
            current = None
        elif role == "decor":
            if text not in ("■", "□"):
                parts.append(("decor", text))
        else:
            if current is not None:
                current[-1].append(text)
            elif parts and parts[-1][0] == "body":
                parts[-1][1].append(text)
            else:
                parts.append(("body", [text]))
    result = [tuple(part) for part in parts]
    # Страница-список тем: «Тема: …» без раздела и постов под ней — строка перечня.
    for position, part in enumerate(result):
        following = result[position + 1][0] if position + 1 < len(result) else ""
        if part[0] == "topic" and not part[2] and following in ("topic", "list"):
            result[position] = ("list", "Тема", part[1])
    return result


def _luminance(color: str) -> float:
    try:
        channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    except (TypeError, ValueError):
        return 0.0
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(first: str, second: str) -> float:
    high, low = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


# Мелкий приглушённый текст (раздел, дата, страницы) — не бледнее AA 4,5:1.
_MUTED_CONTRAST = 4.5


def _forum_colors(template) -> dict:
    background, text, accent = template["background"], template["text"], template["accent"]
    header = _mix(background, accent, 0.16)
    muted = text
    for share in (0.42, 0.36, 0.3, 0.24, 0.18, 0.12, 0.06):
        candidate = _mix(text, background, share)
        if min(_contrast(candidate, header), _contrast(candidate, background)) >= _MUTED_CONTRAST:
            muted = candidate
            break
    return {"header": header, "line": _mix(background, text, 0.18), "muted": muted}


_FORUM_STRONG_TAGS = re.compile(r"модератор|проверенн|подтвержд|verified|moderator|агент скп|pr скп", re.I)


def _render_forum(texts, template, source_html) -> str:
    colors = _forum_colors(template)
    accent, line, muted = template["accent"], colors["line"], colors["muted"]
    row = f"padding:7px 12px;border-top:1px solid {line};"
    pieces: list[str] = []
    for part in forum_structure(texts):
        kind = part[0]
        if kind == "welcome":
            body = "<br />".join(_escape(text) for text in part[1])
            pieces.append(f'<div style="padding:6px 12px;font-size:0.8em;color:{muted};">{body}</div>')
        elif kind == "topic":
            board = f'<br /><span style="font-size:0.85em;color:{muted};">{_escape(part[2])}</span>' if part[2] else ""
            pieces.append(
                f'<div style="padding:8px 12px;background:{colors["header"]};border-top:1px solid {line};">'
                f'<b style="color:{accent};">♦ {_escape(part[1])}</b>{board}</div>'
            )
        elif kind == "pm":
            body = "<br />".join(_escape(text) for text in part[2])
            pieces.append(
                f'<div style="padding:8px 12px;background:{colors["header"]};border-top:1px solid {line};">'
                f'<b style="color:{accent};">✉ {_escape(part[1])}</b></div>'
                + (f'<div style="{row}">{body}</div>' if body else "")
            )
        elif kind == "post":
            _kind, name, tags, time, body = part
            badges = "".join(
                f'<span style="font-size:0.75em;color:{accent if _FORUM_STRONG_TAGS.search(tag) else muted};'
                f'border:1px solid {line};padding:0 5px;margin-left:5px;">{_escape(tag)}</span>'
                for tag in tags
            )
            head = f'<b style="color:{accent};">{_escape(name)}</b>{badges}' if name else ""
            stamp = f'<span style="font-size:0.8em;color:{muted};">{_escape(time)}</span>' if time else ""
            header = "<br />".join(piece for piece in (head, stamp) if piece)
            text = "<br />".join(_escape(item) for item in body)
            content = header + (f'<div style="margin-top:4px;">{text}</div>' if text else "")
            pieces.append(f'<div style="{row}">{content}</div>')
        elif kind == "list":
            pieces.append(
                f'<div style="{row}"><span style="color:{muted};">{_escape(part[1])}:</span> {_escape(part[2])}</div>'
            )
        elif kind == "page":
            pieces.append(f'<div style="{row}text-align:center;font-size:0.85em;color:{muted};">{_escape(part[1])}</div>')
        elif kind == "decor":
            pieces.append(f'<div style="{row}text-align:center;color:{muted};">{_escape(part[1])}</div>')
        elif kind == "body":
            pieces.append(f'<div style="{row}">' + "<br />".join(_escape(text) for text in part[1]) + "</div>")
    style = (
        f"margin:16px 0;border:2px solid {template['border']};border-left:8px solid {template['border']};"
        f"background:{template['background']};color:{template['text']};text-align:left;line-height:1.5;"
    )
    block = f'<div {BLOCK_ATTR}="forum"{_original_attr(source_html)} style="{style}">' + "".join(pieces) + "</div>"
    return re.sub(r"\s*\n\s*", " ", block)


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
    if kind == "chat":
        return _render_chat([text for text in texts if text], template, source_html)
    if kind == "forum":
        return _render_forum([text for text in texts if text], template, source_html)
    items = [item for text in texts if text for item in _card_pieces(text)]
    texts = [text for text, _ in items]
    heads = [head for _, head in items]
    if texts and bracket_shape(texts[0]) not in ("keyed", "list"):
        texts[0] = _strip_outer_quotes(strip_brackets(texts[0]))
    title = None
    # Несколько карточек в одной рамке: названия одинаково выделены строками,
    # а не первое заголовком рамки.
    if len(texts) >= 2 and sum(heads) <= 1 and _looks_like_title(texts[0]):
        title = _strip_outer_quotes(texts[0].rstrip(":：").strip())
        texts, heads = texts[1:], heads[1:]

    rows = []
    card = sum(1 for text in texts if is_key_value(text)) >= 2
    for text, head in zip(texts, heads):
        if head:
            name = _strip_outer_quotes(strip_brackets(text))
            rows.append((f'<b style="color:{accent};">{_escape(name)}</b>', False))
        else:
            rows.extend(_render_row(text, accent, italic_allowed=title is not None, card=card))
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
    block = f'<div {BLOCK_ATTR}="{kind}"{_original_attr(source_html)} style="{style}">' + "<br />".join(pieces) + "</div>"
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


def window_templates(templates, candidate):
    """Шаблоны с читающим этого окна чата (см. :func:`window_readers`)."""
    if candidate.kind != "chat":
        return templates
    base = templates or DEFAULT_TEMPLATES
    chat = dict(base.get("chat", DEFAULT_TEMPLATES["chat"]))
    chat["readers"] = window_readers(candidate, chat)
    return {**base, "chat": chat}


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
            templates=window_templates(templates, candidate),
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


_BLOCK_OPEN_RE = re.compile(rf'<div\b[^>]*\b{BLOCK_ATTR}="[^"]*"[^>]*>', re.I)
_DIV_TAG_RE = re.compile(r"<(/?)div\b[^>]*?(/?)>", re.I)


def iter_block_spans(html: str):
    """(начало, конец) блоков оформления, с учётом вложенных ``div`` (карточки форума).

    Оригинал в ``data-sys-orig`` экранирован, поэтому его теги счёту не мешают.
    """
    position = 0
    while True:
        opening = _BLOCK_OPEN_RE.search(html, position)
        if opening is None:
            return
        depth, end = 0, None
        for tag in _DIV_TAG_RE.finditer(html, opening.start()):
            if tag.group(2):
                continue
            depth += -1 if tag.group(1) else 1
            if depth == 0:
                end = tag.end()
                break
        if end is None:
            return
        yield opening.start(), end
        position = end


def replace_blocks(html: str, replace) -> str:
    """Заменить каждый блок оформления на ``replace(блок)``."""
    pieces, last = [], 0
    for start, end in iter_block_spans(html):
        pieces.append(html[last:start])
        pieces.append(replace(html[start:end]))
        last = end
    pieces.append(html[last:])
    return "".join(pieces)


def strip_windows(html: str) -> tuple[str, int]:
    """Снять оформление: вернуть исходные абзацы из ``data-sys-orig``.

    Блок без сохранённого оригинала разбирается на абзацы по ``<br />``.
    """
    count = 0

    def restore(block):
        nonlocal count
        original = _ORIG_RE.search(block)
        count += 1
        if original is None:
            return _fallback_paragraphs(block)
        value = original.group(1) if original.group(1) is not None else original.group(2)
        return html_module.unescape(value)

    return replace_blocks(html, restore), count


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
    conventions = book_bracket_conventions(entry[2] for entry in entries)
    if any(conventions.values()):
        settings = replace(settings or DetectorSettings(), **conventions)
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
    # Главы, где есть похожие на чат серии без шапки и без повторов: их
    # пересмотрим, когда станут известны собеседники из переписок всей книги.
    pending_chats = []
    for index, ((original, path, html, _source_html), marks) in enumerate(zip(entries, marks_by_chapter), start=1):
        source_marks = set().union(*(indices for family, indices in marks.items() if family in trusted)) if marks else set()
        weak_chats: list = []
        candidates = find_windows(html, settings, source_marks=source_marks if marks else None, weak_chats=weak_chats)
        if weak_chats:
            pending_chats.append((len(result), html, source_marks if marks else None))
        result.append(ChapterScan(
            original=original,
            path=path,
            title=_chapter_title(html, original),
            candidates=candidates,
        ))
        if progress is not None:
            progress((total if has_source else 0) + index, steps, original)
    participants = chat_participants(
        candidate.lines for scan in result for candidate in scan.candidates if candidate.kind == "chat"
    )
    if participants and pending_chats:
        base = settings or DetectorSettings()
        chat_settings = replace(base, chat_participants=frozenset(base.chat_participants) | participants)
        for position, html, source_marks in pending_chats:
            scan = result[position]
            result[position] = ChapterScan(
                original=scan.original,
                path=scan.path,
                title=scan.title,
                candidates=find_windows(html, chat_settings, source_marks=source_marks),
            )
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
    "chat": [
        "Групповой чат: Отряд", "[Анн]: Кто сегодня идёт в Мементос?", "[Рюдзи]: Я!",
        "[Рюдзи]: Только после уроков.", "[Кен]: Буду к шести.", "[Анн]: Отлично, ждём.",
    ],
    "forum": [
        "♦ Тема: Новый кейп в Броктон-Бей", "В: Форумы ► Места ► Америка ► Броктон-Бей",
        "Баграт (Автор темы) (Ветеран форума)", "Опубликовано 16 февраля 2011:",
        "Вчера у Набережной видели нового кейпа. Кто-нибудь знает, кто это?",
        "(Показана страница 1 из 3)",
        "► Рив (Подтверждённый кейп)", "Ответил 16 февраля 2011:",
        "СКП не комментирует.",
        "Конец страницы. 1, 2, 3",
    ],
}
# Кто читает чат в образце, если в шаблоне никто не указан.
SAMPLE_CHAT_READER = "Кен"


def with_sample_reader(templates):
    """Шаблоны для образцов: без указанного читателя чат образца читает «Кен»."""
    templates = {kind: dict(template) for kind, template in (templates or DEFAULT_TEMPLATES).items()}
    chat = templates.setdefault("chat", dict(DEFAULT_TEMPLATES["chat"]))
    if not chat_readers(chat):
        chat["readers"] = [SAMPLE_CHAT_READER]
    return templates


def render_preview_document(templates=None, extra=None, extra_templates=None) -> str:
    """Самостоятельная HTML-страница с рамками для точного просмотра в браузере.

    ``extra`` — список ``(строки, тип)``, показывается перед образцами всех
    типов. Блоки те же, что уйдут в главы, но без ``data-sys-orig``.
    """
    blocks = [render_window(lines, kind, templates=extra_templates or templates) for lines, kind in (extra or [])]
    samples = with_sample_reader(templates)
    blocks.extend(render_window(lines, kind, templates=samples) for kind, lines in SAMPLE_WINDOWS.items())
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
