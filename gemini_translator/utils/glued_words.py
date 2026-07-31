from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from html.parser import HTMLParser


RUSSIAN_WORD_RE = re.compile(r"[А-Яа-яЁё]{4,}")
CYRILLIC_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[а-яё])(?=[А-ЯЁ])")
ONE_LETTER_WORDS = frozenset({"а", "в", "и", "к", "о", "с", "у", "я"})
SENTENCE_BOUNDARY_CHARS = frozenset(".!?…\n\r—–─:;([{«„\"")
EXCLUDED_HTML_TAGS = frozenset({
    "code",
    "head",
    "kbd",
    "math",
    "pre",
    "samp",
    "script",
    "style",
    "svg",
    "textarea",
    "title",
    "var",
})
HYPHENATED_SUFFIXES = frozenset({"ка", "либо", "нибудь", "то"})


@dataclass(frozen=True)
class GluedWordCandidate:
    start: int
    end: int
    original: str
    replacement: str | None
    alternatives: tuple[str, ...]

    @property
    def confident(self) -> bool:
        return bool(self.replacement)


@lru_cache(maxsize=1)
def _default_morph_analyzer():
    try:
        import pymorphy3

        return pymorphy3.MorphAnalyzer(lang="ru")
    except Exception:
        return None


def _is_sentence_initial(text: str, start: int) -> bool:
    index = start - 1
    while index >= 0 and text[index].isspace():
        index -= 1
    return index < 0 or text[index] in SENTENCE_BOUNDARY_CHARS


def find_glued_russian_words(text: str, analyzer=None) -> list[GluedWordCandidate]:
    """Find likely missing spaces without guessing between ambiguous splits."""
    if not isinstance(text, str) or not text:
        return []

    if analyzer is None:
        analyzer = _default_morph_analyzer()

    known_cache: dict[str, bool] = {}

    def is_known(word: str) -> bool:
        normalized = word.lower().replace("ё", "е")
        if len(normalized) == 1:
            return normalized in ONE_LETTER_WORDS
        if analyzer is None:
            return False
        if normalized not in known_cache:
            try:
                known_cache[normalized] = bool(analyzer.word_is_known(normalized))
            except Exception:
                known_cache[normalized] = False
        return known_cache[normalized]

    candidates = []
    for match in RUSSIAN_WORD_RE.finditer(text):
        word = match.group(0)
        if (
            match.start() > 0
            and text[match.start() - 1] == "-"
            and word.lower().replace("ё", "е") in HYPHENATED_SUFFIXES
        ):
            continue

        camel_replacement = CYRILLIC_CAMEL_BOUNDARY_RE.sub(" ", word)
        if camel_replacement != word:
            candidates.append(GluedWordCandidate(
                start=match.start(),
                end=match.end(),
                original=word,
                replacement=camel_replacement,
                alternatives=(camel_replacement,),
            ))
            continue

        if analyzer is None or is_known(word):
            continue
        if word[0].isupper() and not _is_sentence_initial(text, match.start()):
            # Unknown title-cased words in the middle of a sentence are usually
            # names or glossary terms. CamelCase above remains safe to repair.
            continue

        alternatives = []
        for split_at in range(1, len(word)):
            left = word[:split_at]
            right = word[split_at:]
            if is_known(left) and is_known(right):
                alternatives.append(f"{left} {right}")

        unique_alternatives = tuple(dict.fromkeys(alternatives))
        replacement = unique_alternatives[0] if len(unique_alternatives) == 1 else None
        if unique_alternatives:
            candidates.append(GluedWordCandidate(
                start=match.start(),
                end=match.end(),
                original=word,
                replacement=replacement,
                alternatives=unique_alternatives,
            ))

    return candidates


def repair_glued_russian_words(text: str, analyzer=None) -> tuple[str, list[GluedWordCandidate]]:
    candidates = find_glued_russian_words(text, analyzer=analyzer)
    confident = [candidate for candidate in candidates if candidate.confident]
    if not confident:
        return text, candidates

    repaired = text
    for candidate in reversed(confident):
        repaired = repaired[:candidate.start] + candidate.replacement + repaired[candidate.end:]
    return repaired, candidates


class _VisibleTextRepairParser(HTMLParser):
    def __init__(self, source: str, analyzer=None):
        super().__init__(convert_charrefs=False)
        self.source = source
        self.analyzer = analyzer
        self.replacements: list[tuple[int, int, str]] = []
        self.candidates: list[GluedWordCandidate] = []
        self._excluded_depth = 0
        self._line_offsets = []
        offset = 0
        for line in source.splitlines(keepends=True):
            self._line_offsets.append(offset)
            offset += len(line)
        if not self._line_offsets:
            self._line_offsets.append(0)

    def _raw_offset(self) -> int:
        line, column = self.getpos()
        line_index = max(0, min(line - 1, len(self._line_offsets) - 1))
        return self._line_offsets[line_index] + column

    def handle_starttag(self, tag, attrs):
        if tag.lower() in EXCLUDED_HTML_TAGS:
            self._excluded_depth += 1

    def handle_startendtag(self, tag, attrs):
        return

    def handle_endtag(self, tag):
        if tag.lower() in EXCLUDED_HTML_TAGS and self._excluded_depth:
            self._excluded_depth -= 1

    def handle_data(self, data):
        if self._excluded_depth or not data:
            return
        repaired, local_candidates = repair_glued_russian_words(data, analyzer=self.analyzer)
        raw_start = self._raw_offset()
        for candidate in local_candidates:
            self.candidates.append(GluedWordCandidate(
                start=raw_start + candidate.start,
                end=raw_start + candidate.end,
                original=candidate.original,
                replacement=candidate.replacement,
                alternatives=candidate.alternatives,
            ))
        if repaired != data:
            self.replacements.append((raw_start, raw_start + len(data), repaired))


def repair_glued_russian_words_in_html(
    html_content: str,
    analyzer=None,
) -> tuple[str, list[GluedWordCandidate]]:
    """Repair visible text while preserving tags, attributes and comments byte-for-byte."""
    if not isinstance(html_content, str) or not html_content:
        return html_content, []

    parser = _VisibleTextRepairParser(html_content, analyzer=analyzer)
    try:
        parser.feed(html_content)
        parser.close()
    except Exception:
        return html_content, []

    repaired = html_content
    for start, end, replacement in reversed(parser.replacements):
        repaired = repaired[:start] + replacement + repaired[end:]
    return repaired, parser.candidates
