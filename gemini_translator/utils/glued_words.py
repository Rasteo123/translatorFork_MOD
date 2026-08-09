from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from html.parser import HTMLParser
from typing import Iterable


RUSSIAN_WORD_RE = re.compile(r"[А-Яа-яЁё]{4,}")
CYRILLIC_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[а-яё])(?=[А-ЯЁ])")
CYRILLIC_TERM_RE = re.compile(r"^[А-Яа-яЁё]+(?:\s+[А-Яа-яЁё]+){0,3}$")
ONE_LETTER_WORDS = frozenset({"а", "в", "и", "к", "о", "с", "у", "я"})
RUSSIAN_CONSONANTS = frozenset("бвгджзклмнпрстфхцчшщ")
SENTENCE_BOUNDARY_CHARS = frozenset(".!?…\n\r—–─:;([{«„\"")
MAX_SEGMENT_PARTS = 4
MAX_SEGMENT_ALTERNATIVES = 32
MAX_ANALYZED_WORD_LENGTH = 64
SHORT_SEGMENT_GRAMMEMES = frozenset({"Abbr", "Fixd"})
PROPER_SEGMENT_GRAMMEMES = frozenset({"Name", "Surn", "Patr", "Geox", "Orgn", "Trad"})
NOMINAL_POS = frozenset({"NOUN", "NPRO", "ADJF", "PRTF", "NUMR"})
VERBAL_POS = frozenset({"VERB", "INFN", "GRND", "PRTS"})
FORCED_FUNCTION_POS = {
    **{
        word: "PREP"
        for word in (
            "без", "в", "во", "до", "за", "из", "к", "ко", "на", "над",
            "о", "об", "от", "по", "под", "при", "про", "с", "со", "у",
        )
    },
    **{word: "CONJ" for word in ("а", "да", "и", "или", "как", "но", "словно")},
    **{word: "PRCL" for word in ("бы", "же", "ли", "не", "ни", "лишь", "ведь", "даже")},
    **{
        word: "NPRO"
        for word in (
            "я", "ты", "вы", "мы", "он", "она", "оно", "они", "его", "ее", "ему",
            "им", "их", "кто", "что", "мне", "нас", "вас", "нам", "вам",
            "ним", "них", "это",
        )
    },
    **{
        word: "ADJF"
        for word in (
            "все", "всё", "тот", "та", "те", "той", "том", "тем", "тех",
            "один", "одна", "одно", "одни", "одного", "одной", "одному",
            "одним", "одних", "какой", "какая", "какое", "какие",
        )
    },
    **{
        word: "ADVB"
        for word in (
            "так", "там", "тут", "где", "уже", "еще", "затем", "обратно",
            "совершенно", "просто", "прямо",
        )
    },
    "больше": "COMP",
    **{
        word: "VERB"
        for word in (
            "был", "была", "было", "были", "буду", "будешь", "будет",
            "будем", "будете", "будут",
        )
    },
}
BOUNDARY_ANCHOR_WORDS = frozenset(FORCED_FUNCTION_POS)
COMMON_PAIR_BONUSES = {
    ("даже", "не"): 4,
    ("просто", "так"): 4,
    ("тот", "еще"): 4,
    ("в", "середине"): 2,
}
COMMON_SEQUENCE_BONUSES = {
    "несмотря ни на что": 6,
}
HYPHENATED_ADVERB_ENDINGS = ("ому", "ему", "ски", "цки", "ьи")
HYPHENATED_ORDINAL_ADVERBS = frozenset({
    "первых",
    "вторых",
    "третьих",
    "четвертых",
    "пятых",
    "шестых",
    "седьмых",
    "восьмых",
    "девятых",
    "десятых",
})
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
ORDINAL_DIGIT_SUFFIXES = ("го", "му", "ми", "й", "я", "е", "м", "х", "ю")
INLINE_HTML_TAGS = (
    "a",
    "b",
    "cite",
    "del",
    "em",
    "i",
    "ins",
    "mark",
    "q",
    "small",
    "span",
    "strong",
    "sub",
    "sup",
    "u",
)
INLINE_TAG_BOUNDARY_RE = re.compile(
    rf"(?P<left>[А-Яа-яЁё]{{1,{MAX_ANALYZED_WORD_LENGTH}}})"
    rf"(?P<markup>(?:</?(?:{'|'.join(INLINE_HTML_TAGS)})\b[^>]*>)+)"
    rf"(?P<right>[А-Яа-яЁё]{{1,{MAX_ANALYZED_WORD_LENGTH}}})",
    re.IGNORECASE,
)


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


@dataclass(frozen=True)
class _MorphProfile:
    poses: frozenset[str]
    top_pos: str | None
    top_grammemes: frozenset[str]
    top_score: float
    method_names: tuple[str, ...]


@lru_cache(maxsize=1)
def _default_morph_analyzer():
    # Общий анализатор приложения (utils.morphology) — раньше здесь строился
    # ВТОРОЙ независимый словарь pymorphy3 (~35МБ дубликат).
    from .morphology import get_morph_analyzer
    return get_morph_analyzer()


def _is_sentence_initial(text: str, start: int) -> bool:
    index = start - 1
    while index >= 0 and text[index].isspace():
        index -= 1
    return index < 0 or text[index] in SENTENCE_BOUNDARY_CHARS


def _normalize_cyrillic(value: str) -> str:
    return str(value or "").casefold().replace("ё", "е")


@lru_cache(maxsize=32)
def _cached_protected_term_rules(
    protected_terms: tuple[str, ...],
) -> tuple[
    frozenset[str],
    dict[str, tuple[tuple[int, ...], ...]],
    dict[str, tuple[bool, ...]],
]:
    exact_terms: set[str] = set()
    joined_term_boundaries: dict[str, list[tuple[int, ...]]] = {}
    protected_forms: dict[str, set[bool]] = {}
    analyzer = _default_morph_analyzer()

    for raw_term in protected_terms or ():
        term = re.sub(r"\s+", " ", str(raw_term or "").strip())
        if not term or not CYRILLIC_TERM_RE.fullmatch(term):
            continue

        parts = term.split(" ")
        joined_key = _normalize_cyrillic("".join(parts))
        if len(parts) == 1:
            exact_terms.add(joined_key)
            starts_upper = term[0].isupper()
            protected_forms.setdefault(joined_key, set()).add(starts_upper)
            if starts_upper and joined_key[-1:] in RUSSIAN_CONSONANTS:
                for ending in ("а", "у", "ом", "е"):
                    protected_forms.setdefault(joined_key + ending, set()).add(True)

            parse = getattr(analyzer, "parse", None)
            if callable(parse):
                try:
                    parses = parse(term)
                except Exception:
                    parses = ()
                for parsed in parses[:16]:
                    tag = getattr(parsed, "tag", None)
                    if getattr(tag, "POS", None) not in {"NOUN", "ADJF"}:
                        continue
                    try:
                        lexeme = parsed.lexeme
                    except Exception:
                        continue
                    lexeme_words = {
                        _normalize_cyrillic(getattr(form, "word", ""))
                        for form in lexeme
                    }
                    if joined_key not in lexeme_words:
                        continue
                    for form in lexeme_words:
                        if joined_key[-1:] in RUSSIAN_CONSONANTS:
                            allowed_ending = (
                                form[len(joined_key):]
                                if form.startswith(joined_key)
                                else None
                            )
                            if allowed_ending not in {
                                "", "а", "у", "ом", "е", "ы", "и",
                                "ов", "ам", "ами", "ах",
                            }:
                                continue
                        if form:
                            protected_forms.setdefault(form, set()).add(starts_upper)
            continue

        boundaries = tuple(
            sum(len(part) for part in parts[:index])
            for index in range(1, len(parts))
        )
        joined_term_boundaries.setdefault(joined_key, []).append(boundaries)

        # Capitalized components of an approved multiword term can also mark
        # a safe boundary in an inflected or extended phrase (for example,
        # ``Старший Адепткласса`` -> ``Старший Адепт класса``).
        for part in parts:
            if len(part) >= 4 and part[0].isupper():
                protected_forms.setdefault(_normalize_cyrillic(part), set()).add(True)

    return (
        frozenset(exact_terms),
        {
            key: tuple(dict.fromkeys(boundaries))
            for key, boundaries in joined_term_boundaries.items()
        },
        {
            form: tuple(sorted(case_variants))
            for form, case_variants in protected_forms.items()
        },
    )


def _protected_term_rules(
    protected_terms: Iterable[str] | None,
) -> tuple[
    frozenset[str],
    dict[str, tuple[tuple[int, ...], ...]],
    dict[str, tuple[bool, ...]],
]:
    cache_key = tuple(sorted({
        str(term or "").strip()
        for term in protected_terms or ()
        if str(term or "").strip()
    }))
    return _cached_protected_term_rules(cache_key)


def _insert_spaces_at_boundaries(word: str, boundaries: tuple[int, ...]) -> str:
    pieces = []
    previous = 0
    for boundary in boundaries:
        pieces.append(word[previous:boundary])
        previous = boundary
    pieces.append(word[previous:])
    return " ".join(pieces)


def _morph_profile(
    word: str,
    analyzer,
    cache: dict[str, _MorphProfile | None],
) -> _MorphProfile | None:
    normalized = _normalize_cyrillic(word)
    if normalized in cache:
        return cache[normalized]

    parse = getattr(analyzer, "parse", None)
    if not callable(parse):
        cache[normalized] = None
        return None
    try:
        parses = parse(normalized)
    except Exception:
        parses = ()
    if not parses:
        cache[normalized] = None
        return None

    top = parses[0]
    top_tag = getattr(top, "tag", None)
    profile = _MorphProfile(
        poses=frozenset(
            getattr(getattr(parsed, "tag", None), "POS", None)
            for parsed in parses[:8]
            if getattr(getattr(parsed, "tag", None), "POS", None)
        ),
        top_pos=getattr(top_tag, "POS", None),
        top_grammemes=frozenset(getattr(top_tag, "grammemes", ()) or ()),
        top_score=float(getattr(top, "score", 0.0) or 0.0),
        method_names=tuple(
            type(item[0]).__name__
            for item in (getattr(top, "methods_stack", ()) or ())
            if item
        ),
    )
    cache[normalized] = profile
    return profile


def _segmentation_pair_score(
    left: str,
    right: str,
    analyzer,
    profile_cache: dict[str, _MorphProfile | None],
) -> int:
    left_normalized = _normalize_cyrillic(left)
    right_normalized = _normalize_cyrillic(right)
    left_profile = _morph_profile(left, analyzer, profile_cache)
    right_profile = _morph_profile(right, analyzer, profile_cache)
    left_poses = frozenset({FORCED_FUNCTION_POS[left_normalized]}) if left_normalized in FORCED_FUNCTION_POS else (
        left_profile.poses if left_profile else frozenset()
    )
    right_poses = frozenset({FORCED_FUNCTION_POS[right_normalized]}) if right_normalized in FORCED_FUNCTION_POS else (
        right_profile.poses if right_profile else frozenset()
    )
    if not left_poses or not right_poses:
        return 0

    scores = []
    for left_pos in left_poses:
        for right_pos in right_poses:
            score = 0
            if left_pos == "PREP":
                if right_pos in NOMINAL_POS:
                    score = 3
                elif right_pos == "ADVB":
                    score = 0
                else:
                    score = -4
            elif left_pos == "PRCL" and left_normalized in {"не", "ни"}:
                score = 3 if right_pos in VERBAL_POS | {"ADJF", "ADJS", "PRTF", "ADVB", "COMP"} else 0
            elif left_pos == "CONJ":
                score = 1
            elif left_pos == "NPRO" and right_pos in {"NOUN", "VERB", "PRTS"}:
                score = 2
            elif left_pos == "NPRO" and right_pos == "PRCL" and right_normalized in {"не", "ни"}:
                score = 2
            elif left_pos in {"NOUN", "NPRO"} and right_pos in {"VERB", "PRTS"}:
                score = 2
            elif left_pos in {"ADJF", "PRTF"} and right_pos == "NOUN":
                score = 2
            elif left_pos == "ADJF" and right_pos == "ADJF":
                score = 1
            elif left_pos == "NOUN" and right_pos == "ADJF":
                score = 1
            elif left_pos in {"VERB", "GRND"} and right_pos in NOMINAL_POS | {"ADVB", "PREP"}:
                score = 2
            elif left_pos == "VERB" and right_pos == "VERB":
                score = -2
            elif left_pos == "ADVB" and right_pos in {"PREP", "PRCL", "VERB", "ADJF", "ADVB", "COMP"}:
                score = 1
            scores.append(score)
    return max(scores)


def _segmentation_score(
    alternative: str,
    analyzer,
    profile_cache: dict[str, _MorphProfile | None],
) -> int:
    parts = alternative.split()
    score = 0
    normalized_parts = [_normalize_cyrillic(part) for part in parts]

    for part, normalized in zip(parts, normalized_parts):
        profile = _morph_profile(part, analyzer, profile_cache)
        if len(normalized) <= 2 and normalized not in BOUNDARY_ANCHOR_WORDS:
            score -= 4
        elif len(normalized) == 3 and normalized not in BOUNDARY_ANCHOR_WORDS:
            score -= 2
        if profile and profile.top_score < 0.15:
            score -= 1

    for index, (left, right) in enumerate(zip(parts, parts[1:])):
        left_normalized = normalized_parts[index]
        right_normalized = normalized_parts[index + 1]
        if left_normalized in BOUNDARY_ANCHOR_WORDS or right_normalized in BOUNDARY_ANCHOR_WORDS:
            score += 2
        score += _segmentation_pair_score(left, right, analyzer, profile_cache)
        score += COMMON_PAIR_BONUSES.get((left_normalized, right_normalized), 0)

    if len(parts) >= 3:
        score -= len(parts) - 2
        if not any(part in BOUNDARY_ANCHOR_WORDS for part in normalized_parts):
            score -= 4
    score += COMMON_SEQUENCE_BONUSES.get(" ".join(normalized_parts), 0)
    return score


def _looks_like_productive_single_word(
    word: str,
    analyzer,
    profile_cache: dict[str, _MorphProfile | None],
) -> bool:
    profile = _morph_profile(word, analyzer, profile_cache)
    if not profile:
        return False
    methods = set(profile.method_names)
    return "DictionaryAnalyzer" in methods and "UnknownPrefixAnalyzer" in methods


def _dictionary_segmentations(word: str, is_known) -> tuple[str, ...]:
    """Return up to a bounded number of 2-4 word dictionary segmentations."""
    word_length = len(word)
    collected: list[str] = []

    @lru_cache(maxsize=None)
    def suffixes(start: int, parts_left: int) -> tuple[tuple[str, ...], ...]:
        if parts_left == 0:
            return ((),) if start == word_length else ()

        remaining_chars = word_length - start
        if remaining_chars < parts_left:
            return ()

        results: list[tuple[str, ...]] = []
        latest_end = word_length - (parts_left - 1)
        for end in range(start + 1, latest_end + 1):
            part = word[start:end]
            if not is_known(part):
                continue
            for suffix in suffixes(end, parts_left - 1):
                results.append((part, *suffix))
                if len(results) > MAX_SEGMENT_ALTERNATIVES:
                    return tuple(results)
        return tuple(results)

    for part_count in range(2, MAX_SEGMENT_PARTS + 1):
        part_count_results: list[str] = []
        for parts in suffixes(0, part_count):
            part_count_results.append(" ".join(parts))
            if len(part_count_results) > MAX_SEGMENT_ALTERNATIVES:
                break
        if part_count_results:
            # Prefer the smallest number of inserted spaces. This preserves a
            # clear two-word split even when obscure shorter dictionary forms
            # also permit artificial three- or four-word segmentations.
            collected.extend(part_count_results[:MAX_SEGMENT_ALTERNATIVES])
            break

    return tuple(dict.fromkeys(collected))


def find_glued_russian_words(
    text: str,
    analyzer=None,
    protected_terms: Iterable[str] | None = None,
    *,
    _known_cache: dict[str, bool] | None = None,
    _plausible_cache: dict[str, bool] | None = None,
    _profile_cache: dict[str, _MorphProfile | None] | None = None,
    _protected_rules=None,
) -> list[GluedWordCandidate]:
    """Find likely missing spaces, auto-selecting only an unambiguous split."""
    if not isinstance(text, str) or not text:
        return []

    if analyzer is None:
        analyzer = _default_morph_analyzer()

    protected_exact, protected_joined, protected_forms = (
        _protected_rules
        if _protected_rules is not None
        else _protected_term_rules(protected_terms)
    )

    known_cache = _known_cache if _known_cache is not None else {}
    plausible_cache = _plausible_cache if _plausible_cache is not None else {}
    profile_cache = _profile_cache if _profile_cache is not None else {}

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

    def is_plausible_segment(word: str) -> bool:
        normalized = _normalize_cyrillic(word)
        if normalized in plausible_cache:
            return plausible_cache[normalized]
        if not is_known(word):
            plausible_cache[normalized] = False
            return False

        profile = _morph_profile(word, analyzer, profile_cache)
        if profile is None:
            plausible_cache[normalized] = True
            return True
        if profile.top_grammemes & PROPER_SEGMENT_GRAMMEMES:
            plausible_cache[normalized] = False
            return False
        if profile.top_grammemes & SHORT_SEGMENT_GRAMMEMES:
            plausible_cache[normalized] = False
            return False
        plausible_cache[normalized] = True
        return True

    def orthographic_separator_replacement(word: str) -> str | None:
        normalized = _normalize_cyrillic(word)

        for first, second in (("из", "за"), ("из", "под")):
            prefix_length = len(first) + len(second)
            if normalized.startswith(first + second) and len(word) > prefix_length:
                remainder = word[prefix_length:]
                if is_plausible_segment(remainder):
                    return f"{word[:len(first)]}-{word[len(first):prefix_length]} {remainder}"

        for suffix in ("нибудь", "либо", "то", "ка"):
            if not normalized.endswith(suffix) or len(word) <= len(suffix):
                continue
            base = word[:-len(suffix)]
            if is_plausible_segment(base):
                return f"{base}-{word[-len(suffix):]}"

        if normalized.startswith("кое") and len(word) > 3:
            remainder = word[3:]
            if is_plausible_segment(remainder):
                return f"{word[:3]}-{remainder}"

        if normalized.startswith("по") and len(word) > 2:
            remainder = word[2:]
            if normalized.endswith(HYPHENATED_ADVERB_ENDINGS) and is_plausible_segment(remainder):
                return f"{word[:2]}-{remainder}"

        for prefix in ("во", "в"):
            if not normalized.startswith(prefix):
                continue
            remainder = normalized[len(prefix):]
            if remainder in HYPHENATED_ORDINAL_ADVERBS:
                return f"{word[:len(prefix)]}-{word[len(prefix):]}"

        return None

    candidates = []
    for match in RUSSIAN_WORD_RE.finditer(text):
        word = match.group(0)
        normalized_word = _normalize_cyrillic(word)
        if (
            len(word) > MAX_ANALYZED_WORD_LENGTH
            or normalized_word in protected_exact
            or normalized_word in protected_forms
            or (match.end() < len(text) and text[match.end()] == "-")
        ):
            continue
        follows_cyrillic_hyphen = bool(
            match.start() >= 2
            and text[match.start() - 1] == "-"
            and re.fullmatch(r"[А-Яа-яЁё]", text[match.start() - 2])
        )
        follows_digit_hyphen = bool(
            match.start() >= 2
            and text[match.start() - 1] == "-"
            and text[match.start() - 2].isdigit()
        )
        if (
            match.start() > 0
            and follows_cyrillic_hyphen
            and normalized_word in HYPHENATED_SUFFIXES
        ):
            continue

        if follows_cyrillic_hyphen:
            suffix_replacements = []
            for suffix in sorted(HYPHENATED_SUFFIXES, key=len, reverse=True):
                if suffix == "ка":
                    continue
                if not normalized_word.startswith(suffix) or len(word) <= len(suffix):
                    continue
                remainder = word[len(suffix):]
                if is_plausible_segment(remainder):
                    suffix_replacements.append(f"{word[:len(suffix)]} {remainder}")
            if len(suffix_replacements) == 1:
                replacement = suffix_replacements[0]
                candidates.append(GluedWordCandidate(
                    start=match.start(),
                    end=match.end(),
                    original=word,
                    replacement=replacement,
                    alternatives=(replacement,),
                ))
                continue

        if follows_digit_hyphen:
            ordinal_replacements = []
            for suffix in ORDINAL_DIGIT_SUFFIXES:
                if not normalized_word.startswith(suffix) or len(word) <= len(suffix):
                    continue
                remainder = word[len(suffix):]
                if is_plausible_segment(remainder):
                    ordinal_replacements.append(f"{word[:len(suffix)]} {remainder}")
            ordinal_replacements = list(dict.fromkeys(ordinal_replacements))
            if len(ordinal_replacements) == 1:
                replacement = ordinal_replacements[0]
                candidates.append(GluedWordCandidate(
                    start=match.start(),
                    end=match.end(),
                    original=word,
                    replacement=replacement,
                    alternatives=(replacement,),
                ))
                continue

        protected_alternatives = tuple(
            _insert_spaces_at_boundaries(word, boundaries)
            for boundaries in protected_joined.get(normalized_word, ())
        )
        if protected_alternatives:
            candidates.append(GluedWordCandidate(
                start=match.start(),
                end=match.end(),
                original=word,
                replacement=(
                    protected_alternatives[0]
                    if len(protected_alternatives) == 1
                    else None
                ),
                alternatives=protected_alternatives,
            ))
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

        title_case_mid_sentence = bool(
            word[0].isupper() and not _is_sentence_initial(text, match.start())
        )

        protected_boundary_alternatives = []
        for boundary in range(4, len(word)):
            case_variants = protected_forms.get(normalized_word[:boundary])
            if not case_variants or True not in case_variants or not word[0].isupper():
                continue
            remainder = word[boundary:]
            remainder_normalized = _normalize_cyrillic(remainder)
            if (
                len(remainder) <= 3
                and remainder_normalized not in BOUNDARY_ANCHOR_WORDS
            ):
                continue
            if is_plausible_segment(remainder):
                protected_boundary_alternatives.append(
                    f"{word[:boundary]} {remainder}"
                )
        protected_boundary_alternatives = list(dict.fromkeys(protected_boundary_alternatives))
        if protected_boundary_alternatives:
            scored_boundaries = [
                (
                    alternative,
                    _segmentation_score(alternative, analyzer, profile_cache),
                )
                for alternative in protected_boundary_alternatives
            ]
            ranked_boundaries = sorted(
                scored_boundaries,
                key=lambda item: item[1],
                reverse=True,
            )
            replacement = (
                ranked_boundaries[0][0]
                if len(ranked_boundaries) == 1
                or ranked_boundaries[0][1] - ranked_boundaries[1][1] >= 2
                else None
            )
            candidates.append(GluedWordCandidate(
                start=match.start(),
                end=match.end(),
                original=word,
                replacement=replacement,
                alternatives=tuple(protected_boundary_alternatives),
            ))
            continue

        if title_case_mid_sentence and not callable(getattr(analyzer, "parse", None)):
            # Unknown title-cased words in the middle of a sentence are usually
            # names or glossary terms. A real analyzer can still admit only a
            # grammatically strong split below; minimal test analyzers cannot.
            continue

        separator_replacement = orthographic_separator_replacement(word)
        if separator_replacement:
            candidates.append(GluedWordCandidate(
                start=match.start(),
                end=match.end(),
                original=word,
                replacement=separator_replacement,
                alternatives=(separator_replacement,),
            ))
            continue

        unique_alternatives = _dictionary_segmentations(word, is_plausible_segment)
        parse_available = callable(getattr(analyzer, "parse", None))
        if parse_available:
            scored_alternatives = []
            for alternative in unique_alternatives:
                score = _segmentation_score(alternative, analyzer, profile_cache)
                part_count = len(alternative.split())
                if score < 0:
                    continue
                if title_case_mid_sentence and score < 2:
                    continue
                if part_count >= 3 and (
                    score < 6
                    or _looks_like_productive_single_word(word, analyzer, profile_cache)
                ):
                    continue
                scored_alternatives.append((alternative, score))
            unique_alternatives = tuple(alternative for alternative, _score in scored_alternatives)
            if len(scored_alternatives) == 1:
                replacement = scored_alternatives[0][0]
            elif len(scored_alternatives) > 1:
                ranked = sorted(scored_alternatives, key=lambda item: item[1], reverse=True)
                replacement = (
                    ranked[0][0]
                    if ranked[0][1] >= 2 and ranked[0][1] - ranked[1][1] >= 2
                    else None
                )
            else:
                replacement = None
        else:
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


def repair_glued_russian_words(
    text: str,
    analyzer=None,
    protected_terms: Iterable[str] | None = None,
    *,
    _known_cache: dict[str, bool] | None = None,
    _plausible_cache: dict[str, bool] | None = None,
    _profile_cache: dict[str, _MorphProfile | None] | None = None,
    _protected_rules=None,
) -> tuple[str, list[GluedWordCandidate]]:
    candidates = find_glued_russian_words(
        text,
        analyzer=analyzer,
        protected_terms=protected_terms,
        _known_cache=_known_cache,
        _plausible_cache=_plausible_cache,
        _profile_cache=_profile_cache,
        _protected_rules=_protected_rules,
    )
    confident = [candidate for candidate in candidates if candidate.confident]
    if not confident:
        return text, candidates

    repaired = text
    for candidate in reversed(confident):
        repaired = repaired[:candidate.start] + candidate.replacement + repaired[candidate.end:]
    return repaired, candidates


class _VisibleTextRepairParser(HTMLParser):
    def __init__(self, source: str, analyzer=None, protected_terms=None):
        super().__init__(convert_charrefs=False)
        self.source = source
        self.analyzer = analyzer
        self.protected_terms = protected_terms
        self.protected_rules = _protected_term_rules(protected_terms)
        self.known_cache: dict[str, bool] = {}
        self.plausible_cache: dict[str, bool] = {}
        self.profile_cache: dict[str, _MorphProfile | None] = {}
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
        repaired, local_candidates = repair_glued_russian_words(
            data,
            analyzer=self.analyzer,
            protected_terms=self.protected_terms,
            _known_cache=self.known_cache,
            _plausible_cache=self.plausible_cache,
            _profile_cache=self.profile_cache,
            _protected_rules=self.protected_rules,
        )
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
    protected_terms: Iterable[str] | None = None,
) -> tuple[str, list[GluedWordCandidate]]:
    """Repair visible text while preserving tags, attributes and comments byte-for-byte."""
    if not isinstance(html_content, str) or not html_content:
        return html_content, []

    protected_terms = tuple(protected_terms or ())
    parser = _VisibleTextRepairParser(
        html_content,
        analyzer=analyzer,
        protected_terms=protected_terms,
    )
    try:
        parser.feed(html_content)
        parser.close()
    except Exception:
        return html_content, []

    replacements = list(parser.replacements)
    for match in INLINE_TAG_BOUNDARY_RE.finditer(html_content):
        left = match.group("left")
        right = match.group("right")
        combined = left + right
        boundary_replacement = f"{left} {right}"
        boundary_candidates = find_glued_russian_words(
            combined,
            analyzer=analyzer,
            protected_terms=protected_terms,
            _known_cache=parser.known_cache,
            _plausible_cache=parser.plausible_cache,
            _profile_cache=parser.profile_cache,
            _protected_rules=parser.protected_rules,
        )
        if any(
            candidate.original == combined
            and candidate.replacement == boundary_replacement
            for candidate in boundary_candidates
        ):
            markup = match.group("markup")
            closing_then_opening = re.search(
                rf"(?:</(?:{'|'.join(INLINE_HTML_TAGS)})\s*>)+(?=<(?!/))",
                markup,
                re.IGNORECASE,
            )
            if closing_then_opening:
                insertion_at = match.start("markup") + closing_then_opening.end()
            elif markup.startswith("</"):
                insertion_at = match.start("right")
            else:
                insertion_at = match.start("markup")
            replacements.append((insertion_at, insertion_at, " "))

    repaired = html_content
    for start, end, replacement in sorted(replacements, reverse=True):
        repaired = repaired[:start] + replacement + repaired[end:]
    return repaired, parser.candidates
