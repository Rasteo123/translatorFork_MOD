import re

from utils import format_num

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


