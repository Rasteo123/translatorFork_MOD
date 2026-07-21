import re
import ipaddress
from datetime import timedelta
from urllib.parse import urlsplit

from constants import RULATE_URL_PATTERN, URL_PATTERN

def format_num(n):
    """Форматирование числа главы: 5.0 → '5', 5.1 → '5.1'."""
    if isinstance(n, float) and n == int(n):
        return str(int(n))
    return str(n)


def natural_sort_key(s):
    """Ключ для естественной сортировки: 'Ch2' < 'Ch10'."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def validate_url(url: str) -> bool:
    """Проверка, что URL подходит для загрузки глав."""
    return bool(URL_PATTERN.match(url.strip()))


def validate_rulate_url(url: str) -> bool:
    """Проверка, что URL подходит для скачивания с rulate."""
    return bool(RULATE_URL_PATTERN.match(url.strip()))


def is_safe_remote_http_url(url: str) -> bool:
    """Allow remote HTTP(S) resources while rejecting local-file and private-network targets."""
    try:
        parsed = urlsplit(str(url or "").strip())
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            return False
        if parsed.username is not None or parsed.password is not None:
            return False

        hostname = parsed.hostname.rstrip(".").casefold()
        if hostname == "localhost" or hostname.endswith((".localhost", ".local")):
            return False
        try:
            return ipaddress.ip_address(hostname).is_global
        except ValueError:
            return True
    except (TypeError, ValueError):
        return False


def format_timedelta(td: timedelta) -> str:
    """Человекочитаемая строка из timedelta."""
    total_sec = int(td.total_seconds())
    if total_sec < 0:
        return "—"
    hours, remainder = divmod(total_sec, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}ч {minutes:02d}мин"
    if minutes > 0:
        return f"{minutes}мин {seconds:02d}сек"
    return f"{seconds}сек"


_ROMAN_VALUES = {
    "I": 1,
    "V": 5,
    "X": 10,
    "L": 50,
    "C": 100,
    "D": 500,
    "M": 1000,
}

_ROMAN_RE = re.compile(
    r"M{0,4}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$",
    re.IGNORECASE,
)

_RU_CARDINALS = {
    "ноль": 0,
    "один": 1,
    "одна": 1,
    "одно": 1,
    "два": 2,
    "две": 2,
    "три": 3,
    "четыре": 4,
    "пять": 5,
    "шесть": 6,
    "семь": 7,
    "восемь": 8,
    "девять": 9,
    "десять": 10,
    "одиннадцать": 11,
    "двенадцать": 12,
    "тринадцать": 13,
    "четырнадцать": 14,
    "пятнадцать": 15,
    "шестнадцать": 16,
    "семнадцать": 17,
    "восемнадцать": 18,
    "девятнадцать": 19,
}

_RU_TENS = {
    "двадцать": 20,
    "тридцать": 30,
    "сорок": 40,
    "пятьдесят": 50,
    "шестьдесят": 60,
    "семьдесят": 70,
    "восемьдесят": 80,
    "девяносто": 90,
}

_RU_HUNDREDS = {
    "сто": 100,
    "двести": 200,
    "триста": 300,
    "четыреста": 400,
    "пятьсот": 500,
    "шестьсот": 600,
    "семьсот": 700,
    "восемьсот": 800,
    "девятьсот": 900,
}

_RU_ORDINALS = {
    "первый": 1,
    "первая": 1,
    "первое": 1,
    "первые": 1,
    "первую": 1,
    "второй": 2,
    "вторая": 2,
    "второе": 2,
    "вторые": 2,
    "вторую": 2,
    "третий": 3,
    "третья": 3,
    "третье": 3,
    "третьи": 3,
    "третью": 3,
    "четвертый": 4,
    "четвертая": 4,
    "четвертое": 4,
    "четвертые": 4,
    "четвертую": 4,
    "пятый": 5,
    "пятая": 5,
    "пятое": 5,
    "пятые": 5,
    "пятую": 5,
    "шестой": 6,
    "шестая": 6,
    "шестое": 6,
    "шестые": 6,
    "шестую": 6,
    "седьмой": 7,
    "седьмая": 7,
    "седьмое": 7,
    "седьмые": 7,
    "седьмую": 7,
    "восьмой": 8,
    "восьмая": 8,
    "восьмое": 8,
    "восьмые": 8,
    "восьмую": 8,
    "девятый": 9,
    "девятая": 9,
    "девятое": 9,
    "девятые": 9,
    "девятую": 9,
    "десятый": 10,
    "десятая": 10,
    "десятое": 10,
    "десятые": 10,
    "десятую": 10,
    "одиннадцатый": 11,
    "одиннадцатая": 11,
    "одиннадцатое": 11,
    "одиннадцатую": 11,
    "двенадцатый": 12,
    "двенадцатая": 12,
    "двенадцатое": 12,
    "двенадцатую": 12,
    "тринадцатый": 13,
    "тринадцатая": 13,
    "тринадцатое": 13,
    "тринадцатую": 13,
    "четырнадцатый": 14,
    "четырнадцатая": 14,
    "четырнадцатое": 14,
    "четырнадцатую": 14,
    "пятнадцатый": 15,
    "пятнадцатая": 15,
    "пятнадцатое": 15,
    "пятнадцатую": 15,
    "шестнадцатый": 16,
    "шестнадцатая": 16,
    "шестнадцатое": 16,
    "шестнадцатую": 16,
    "семнадцатый": 17,
    "семнадцатая": 17,
    "семнадцатое": 17,
    "семнадцатую": 17,
    "восемнадцатый": 18,
    "восемнадцатая": 18,
    "восемнадцатое": 18,
    "восемнадцатую": 18,
    "девятнадцатый": 19,
    "девятнадцатая": 19,
    "девятнадцатое": 19,
    "девятнадцатую": 19,
    "двадцатый": 20,
    "двадцатая": 20,
    "двадцатое": 20,
    "двадцатую": 20,
    "тридцатый": 30,
    "тридцатая": 30,
    "тридцатое": 30,
    "тридцатую": 30,
    "сороковой": 40,
    "сороковая": 40,
    "сороковое": 40,
    "сороковую": 40,
    "пятидесятый": 50,
    "пятидесятая": 50,
    "пятидесятое": 50,
    "пятидесятую": 50,
    "шестидесятый": 60,
    "шестидесятая": 60,
    "шестидесятое": 60,
    "шестидесятую": 60,
    "семидесятый": 70,
    "семидесятая": 70,
    "семидесятое": 70,
    "семидесятую": 70,
    "восьмидесятый": 80,
    "восьмидесятая": 80,
    "восьмидесятое": 80,
    "восьмидесятую": 80,
    "девяностый": 90,
    "девяностая": 90,
    "девяностое": 90,
    "девяностую": 90,
}

_CHAPTER_MARKER_RE = re.compile(r"(?:Глава|Chapter|Гл|Ch)\s*\.?\s*", re.IGNORECASE)


def _roman_to_int(value: str) -> int | None:
    token = (value or "").upper()
    if not token or not _ROMAN_RE.fullmatch(token):
        return None

    total = 0
    previous = 0
    for char in reversed(token):
        current = _ROMAN_VALUES[char]
        if current < previous:
            total -= current
        else:
            total += current
            previous = current
    return total if total > 0 else None


def _normalize_ru_number_word(value: str) -> str:
    return value.lower().replace("ё", "е")


def _ru_small_number_value(word: str) -> int | None:
    token = _normalize_ru_number_word(word)
    if token in _RU_CARDINALS:
        return _RU_CARDINALS[token]
    return _RU_ORDINALS.get(token)


def _parse_russian_number_words(words: list[str]) -> int | None:
    tokens = [_normalize_ru_number_word(word) for word in words]
    if not tokens:
        return None

    total = 0
    index = 0

    if tokens[index] in _RU_HUNDREDS:
        total += _RU_HUNDREDS[tokens[index]]
        index += 1
        if index >= len(tokens):
            return total

    if tokens[index] in _RU_TENS:
        total += _RU_TENS[tokens[index]]
        index += 1
        if index >= len(tokens):
            return total

    if index < len(tokens):
        value = _ru_small_number_value(tokens[index])
        if value is None:
            return None
        total += value
        index += 1

    return total if index == len(tokens) and total > 0 else None


def _consume_russian_number(text: str) -> tuple[int, int] | None:
    matches = []
    position = 0
    for match in re.finditer(r"[А-ЯЁа-яё]+", text):
        if match.start() != position and text[position: match.start()].strip(" -\t\r\n"):
            break
        matches.append(match)
        position = match.end()
        if len(matches) >= 4:
            break

    for count in range(len(matches), 0, -1):
        value = _parse_russian_number_words([match.group(0) for match in matches[:count]])
        if value is not None:
            return value, matches[count - 1].end()
    return None


def _consume_chapter_number(text: str) -> tuple[float, int] | None:
    leading_len = len(text) - len(text.lstrip())
    tail = text[leading_len:]

    num_m = re.match(r"(\d+(?:[\.,]\d+)?)", tail)
    if num_m:
        return float(num_m.group(1).replace(",", ".")), leading_len + num_m.end()

    roman_m = re.match(r"([IVXLCDM]+)(?![A-Za-z])", tail, re.IGNORECASE)
    if roman_m:
        roman_value = _roman_to_int(roman_m.group(1))
        if roman_value is not None:
            return float(roman_value), leading_len + roman_m.end()

    russian_value = _consume_russian_number(tail)
    if russian_value is not None:
        value, consumed = russian_value
        return float(value), leading_len + consumed
    return None


def _find_prefixed_chapter(text: str, start_only: bool = False):
    marker_iter = [_CHAPTER_MARKER_RE.match(text)] if start_only else _CHAPTER_MARKER_RE.finditer(text)
    for marker_m in marker_iter:
        if not marker_m:
            continue
        parsed = _consume_chapter_number(text[marker_m.end():])
        if parsed is None:
            continue
        chapter_num, consumed = parsed
        return marker_m, chapter_num, text[marker_m.end() + consumed:]
    return None


def _strip_trailing_counters(title: str) -> str:
    title = re.sub(r"\s*\(\d+/\d+\)\s*$", "", title or "")
    title = re.sub(
        r"\s*\[\s*\d[\d\s\u00a0,.]*\s*(?:зн\.?|симв\.?|символ(?:ов|а)?|chars?)\s*\]\s*$",
        "",
        title,
        flags=re.IGNORECASE,
    )
    return title.strip()


def _strip_leading_chapter_label(title: str, dash_chars: str) -> str:
    parsed = _find_prefixed_chapter(title, start_only=True)
    if parsed is None:
        return title
    _marker_m, _chapter_num, raw_tail = parsed
    return re.sub(rf"^[\s.\-:{dash_chars}_]+", "", raw_tail).strip()


def _clean_chapter_title(title: str, dash_chars: str) -> str:
    clean_title = _strip_trailing_counters(title)
    clean_title = _strip_leading_chapter_label(clean_title, dash_chars)
    clean_title = _strip_trailing_counters(clean_title)
    if clean_title.startswith("\u00ab") and clean_title.endswith("\u00bb"):
        clean_title = clean_title[1:-1].strip()
    return clean_title


# ─── Модель данных ───────────────────────────────────────────────────────────

class ChapterData:
    __slots__ = ("volume", "number", "title", "content", "_parse_index", "_num_found")

    def __init__(self, volume: str, number: float, title: str, content: str,
                 _parse_index: int = 0, _num_found: bool = True):
        self.volume = volume
        self.number = number
        self.title = title
        self.content = content
        self._parse_index = _parse_index  # порядок в исходном файле/архиве
        self._num_found = _num_found      # номер явно найден в заголовке/имени

    def __repr__(self):
        title_part = f": {self.title}" if self.title else ""
        return f"Т.{self.volume} Гл.{format_num(self.number)}{title_part}"

    @property
    def content_length(self) -> int:
        return len(self.content)

    @property
    def preview(self) -> str:
        """Первые ~200 символов контента (без HTML-тегов)."""
        clean = re.sub(r"<[^>]+>", "", self.content)
        return clean[:200] + ("…" if len(clean) > 200 else "")

    @property
    def num_found(self) -> bool:
        return self._num_found


def auto_fill_missing_chapter_numbers(chapters: list[ChapterData]) -> None:
    """
    Автозаполнение пропущенных номеров глав.
    Примеры:
    - 100, 101, "Экстра", "Экстра 2" -> 100, 101, 102, 103
    - "Пролог", "�?нтро", 10 -> 8, 9, 10
    """
    if not chapters:
        return

    numbered_idx = [
        i for i, ch in enumerate(chapters)
        if ch.num_found and ch.number > 0
    ]

    if not numbered_idx:
        for i, ch in enumerate(chapters, start=1):
            ch.number = float(i)
        return

    prev_num = None
    for i, ch in enumerate(chapters):
        if ch.num_found and ch.number > 0:
            prev_num = ch.number
            continue

        if prev_num is not None:
            next_num = int(prev_num) + 1
            ch.number = float(next_num)
            prev_num = ch.number
            continue

        # Нет предыдущего номера — выравниваемся от ближайшего следующего явного номера
        next_known_idx = None
        for j in numbered_idx:
            if j > i:
                next_known_idx = j
                break

        if next_known_idx is None:
            ch.number = float(i + 1)
            prev_num = ch.number
            continue

        anchor = int(chapters[next_known_idx].number)
        distance = next_known_idx - i
        candidate = max(1, anchor - distance)
        ch.number = float(candidate)
        prev_num = ch.number


# ─── Парсинг тома/главы ─────────────────────────────────────────────────────

def parse_vol_and_chapter(text: str, default_vol: str, fallback_num: int):
    """
    �?звлечь номер тома, номер главы и чистое название из строки.

    Поддерживаемые форматы:
    - «Том 2 Глава 15: Название»
    - «Vol.3 Chapter 42 — Title»
    - «Т1 Гл.5 Foo»
    - «Ch.12.5 Bar (Часть 2)»
    - Просто число: «15 Название»
    - «Название – Глава 26» (название до номера)
    - «Глава 27: «Название»» (кавычки снимаются)
    - «11. Название (5)» (число + название)
    - «Глава 16 Название (2). Часть 1» (→ 16.1)
    - «Глава 5.1 Название (Часть 2)» (→ 5.12)
    - «Название. Глава 24 (24/536)» (индикатор убирается)
    - «Глава XXIV: Название» и «Глава тридцать пятая: Название»
    """
    detected_vol = default_vol
    chap_num = 0.0
    clean_title = text
    dash_chars = "—–"

    # Том
    vol_m = re.search(r"(?:Том|Vol(?:ume)?|Т|V)\s*\.?\s*(\d+)", text, re.IGNORECASE)
    if vol_m:
        detected_vol = vol_m.group(1)
        text = (text[: vol_m.start()] + text[vol_m.end() :]).strip()

    # Глава
    chapter_match = _find_prefixed_chapter(text)
    if chapter_match:
        ch_m, chap_num, raw_tail = chapter_match
        clean_title = re.sub(rf"^[\s.\-:{dash_chars}_]+", "", raw_tail).strip()
        # Если после номера главы нет названия, проверяем текст ПЕРЕД "Глава N"
        # Например: "Гений призыва из школы некромантов – Глава 26"
        if not clean_title and ch_m.start() > 0:
            prefix = text[: ch_m.start()].strip()
            prefix = re.sub(rf"[\s.\-:{dash_chars}_]+$", "", prefix).strip()
            if prefix:
                clean_title = prefix
    else:
        nums = re.findall(r"(\d+(?:[\.,]\d+)?)", text)
        if nums:
            chap_num = float(nums[0].replace(",", "."))
            idx = text.find(nums[0])
            if idx != -1:
                raw_tail = text[idx + len(nums[0]) :]
                clean_title = re.sub(rf"^[\s.\-:{dash_chars}_]+", "", raw_tail).strip()
        else:
            clean_title = text

    # Запоминаем, был ли номер реально найден в тексте (до fallback)
    num_found = chap_num > 0

    # Часть (Part) → дробный номер:
    #   15 (Часть 2)   → 15.2
    #   5.1 (Часть 2) → 5.12   (к дробной части приклеивается номер части)
    part_match = re.search(
        rf"[\s\-{dash_chars},]*\(?\s*(?:Часть|Part)\s*(\d+)\s*\)?\s*$",
        clean_title,
        re.IGNORECASE,
    )
    if part_match:
        part_num = part_match.group(1)  # строка, не int — для конкатенации
        clean_title = clean_title[: part_match.start()].strip()
        clean_title = re.sub(rf"[\s.\-:{dash_chars}_]+$", "", clean_title)
        if chap_num == 0:
            chap_num = float(fallback_num)
        # Приклеиваем номер части к строковому представлению номера:
        # 5   + Часть 2 → "5.2"
        # 5.1 + Часть 2 → "5.12"
        num_str = format_num(chap_num)  # "5" или "5.1"
        if "." in num_str:
            chap_num = float(f"{num_str}{part_num}")   # 5.1 + 2 → 5.12
        else:
            chap_num = float(f"{num_str}.{part_num}")  # 5 + 2 → 5.2

    if chap_num == 0:
        chap_num = float(fallback_num)

    clean_title = _clean_chapter_title(clean_title, dash_chars)

    return detected_vol, chap_num, clean_title, num_found


