import re
from datetime import timedelta

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
    """
    detected_vol = default_vol
    chap_num = 0.0
    clean_title = text

    # Том
    vol_m = re.search(r"(?:Том|Vol(?:ume)?|Т|V)\s*\.?\s*(\d+)", text, re.IGNORECASE)
    if vol_m:
        detected_vol = vol_m.group(1)
        text = (text[: vol_m.start()] + text[vol_m.end() :]).strip()

    # Глава
    ch_m = re.search(
        r"(?:Глава|Chapter|Ch|Гл)\s*\.?\s*(\d+(?:\.\d+)?)(.*)", text, re.IGNORECASE
    )
    if ch_m:
        chap_num = float(ch_m.group(1))
        raw_tail = ch_m.group(2)
        # Убираем индикатор прогресса вида (24/536) из хвоста
        raw_tail = re.sub(r"\s*\(\d+/\d+\)\s*$", "", raw_tail)
        clean_title = re.sub(r"^[\s.\-:—_]+", "", raw_tail).strip()
        # Если после номера главы нет названия, проверяем текст ПЕРЕД "Глава N"
        # Например: "Гений призыва из школы некромантов – Глава 26"
        if not clean_title and ch_m.start() > 0:
            prefix = text[: ch_m.start()].strip()
            prefix = re.sub(r"[\s.\-:—–_]+$", "", prefix).strip()
            if prefix:
                clean_title = prefix
    else:
        nums = re.findall(r"(\d+(?:\.\d+)?)", text)
        if nums:
            chap_num = float(nums[0])
            idx = text.find(nums[0])
            if idx != -1:
                raw_tail = text[idx + len(nums[0]) :]
                clean_title = re.sub(r"^[\s.\-:—_]+", "", raw_tail).strip()
        else:
            clean_title = text

    # Запоминаем, был ли номер реально найден в тексте (до fallback)
    num_found = chap_num > 0

    # Часть (Part) → дробный номер:
    #   15 (Часть 2)   → 15.2
    #   5.1 (Часть 2) → 5.12   (к дробной части приклеивается номер части)
    part_match = re.search(
        r"[\s\-—,]*\(?\s*(?:Часть|Part)\s*(\d+)\s*\)?\s*$",
        clean_title,
        re.IGNORECASE,
    )
    if part_match:
        part_num = part_match.group(1)  # строка, не int — для конкатенации
        clean_title = clean_title[: part_match.start()].strip()
        clean_title = re.sub(r"[\s.\-:—_]+$", "", clean_title)
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

    # Убираем обрамляющие кавычки «» для единообразия
    # ("Глава 27: «Название»" → "Название")
    if clean_title.startswith("\u00ab") and clean_title.endswith("\u00bb"):
        clean_title = clean_title[1:-1].strip()

    return detected_vol, chap_num, clean_title, num_found


