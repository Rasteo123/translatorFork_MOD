# -*- coding: utf-8 -*-

import hashlib
import json
import os
import re
import time
import zipfile
from collections import Counter, defaultdict

from PyQt6.QtCore import QThread, pyqtSignal

from .language_tools import GlossaryRegexService, LanguageDetector, normalize_glossary_search_text

try:
    from bs4 import BeautifulSoup, UnicodeDammit
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False


TERM_FREQUENCY_CACHE_VERSION = 3


def collect_glossary_originals(glossary_source):
    originals = []
    for entry in glossary_source or []:
        if isinstance(entry, str):
            original = entry
        else:
            original = entry.get("original", "")

        original = str(original or "").strip()
        if original:
            originals.append(original)

    return sorted(set(originals), key=lambda item: item.lower())


def get_epub_signature(epub_path):
    normalized_path = os.path.normcase(os.path.abspath(epub_path)) if epub_path else None
    if not normalized_path:
        return {"path": None, "exists": False}

    try:
        stat_result = os.stat(normalized_path)
    except OSError:
        return {"path": normalized_path, "exists": False}

    return {
        "path": normalized_path,
        "exists": True,
        "size": int(stat_result.st_size),
        "mtime_ns": int(getattr(stat_result, "st_mtime_ns", stat_result.st_mtime * 1_000_000_000)),
    }


def build_term_frequency_fingerprint(glossary_source, epub_path):
    fingerprint_source = {
        "version": TERM_FREQUENCY_CACHE_VERSION,
        "terms": collect_glossary_originals(glossary_source),
        "epub": get_epub_signature(epub_path),
    }
    serialized = json.dumps(
        fingerprint_source,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha1(serialized.encode("utf-8")).hexdigest()


def is_term_frequency_payload_valid(payload, glossary_source, epub_path):
    if not isinstance(payload, dict):
        return False
    if payload.get("version") != TERM_FREQUENCY_CACHE_VERSION:
        return False
    return payload.get("fingerprint") == build_term_frequency_fingerprint(glossary_source, epub_path)


def aggregate_term_frequency_stats(glossary_source, term_occurrences, term_distribution):
    glossary_terms = collect_glossary_originals(glossary_source)

    result_counts = {
        term: int(term_occurrences.get(term, 0))
        for term in glossary_terms
    }
    result_files = {
        term: set(term_distribution.get(term, set()))
        for term in glossary_terms
    }

    for found_term, found_count in term_occurrences.items():
        found_count = int(found_count or 0)
        if found_count <= 0:
            continue

        found_files = set(term_distribution.get(found_term, set()))
        for sub_term in glossary_terms:
            if len(sub_term) >= len(found_term):
                continue
            if sub_term in found_term:
                result_counts[sub_term] = result_counts.get(sub_term, 0) + found_count
                result_files.setdefault(sub_term, set()).update(found_files)

    return {
        term: {
            "count": int(result_counts.get(term, 0)),
            "files": sorted(result_files.get(term, set())),
        }
        for term in glossary_terms
    }


def build_term_frequency_payload(glossary_source, epub_path, term_stats):
    glossary_terms = collect_glossary_originals(glossary_source)
    prepared_terms = {}

    for term in glossary_terms:
        stats = term_stats.get(term, {}) if isinstance(term_stats, dict) else {}
        prepared_terms[term] = {
            "count": int(stats.get("count", 0) or 0),
            "files": sorted(set(stats.get("files", []))),
        }

    return {
        "version": TERM_FREQUENCY_CACHE_VERSION,
        "fingerprint": build_term_frequency_fingerprint(glossary_terms, epub_path),
        "generated_at": int(time.time()),
        "epub_signature": get_epub_signature(epub_path),
        "terms": prepared_terms,
    }


def get_term_frequency_map(payload):
    if not isinstance(payload, dict):
        return {}
    terms = payload.get("terms", {})
    return terms if isinstance(terms, dict) else {}


def get_term_frequency_range(payload):
    frequency_map = get_term_frequency_map(payload)
    if not frequency_map:
        return 0, 0

    counts = [
        int(stats.get("count", 0) or 0)
        for stats in frequency_map.values()
        if isinstance(stats, dict)
    ]
    if not counts:
        return 0, 0

    return min(counts), max(counts)


def _decode_epub_html(raw_content):
    if raw_content is None:
        return ""
    if isinstance(raw_content, str):
        return raw_content

    if BS4_AVAILABLE:
        decoded = UnicodeDammit(raw_content, is_html=True)
        if decoded.unicode_markup is not None:
            return decoded.unicode_markup

    for encoding in ("utf-8-sig", "utf-8", "utf-16", "cp1251", "gb18030", "big5", "shift_jis", "euc-kr"):
        try:
            return raw_content.decode(encoding)
        except UnicodeError:
            continue

    return raw_content.decode("utf-8", errors="replace")


def _extract_text_from_html(raw_content):
    raw_content = _decode_epub_html(raw_content)
    if BS4_AVAILABLE:
        soup = BeautifulSoup(raw_content, "html.parser")
        return soup.get_text(separator=" ")
    return re.sub(r"<[^>]+>", " ", raw_content)


def _frequency_key(text):
    normalized = normalize_glossary_search_text(text)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized.casefold()


def _surface_pattern(surface):
    parts = re.split(r"(\s+)", surface.strip())
    pattern_parts = []
    for part in parts:
        if not part:
            continue
        if part.isspace():
            pattern_parts.append(r"\s+")
        else:
            pattern_parts.append(re.escape(part))
    return "".join(pattern_parts)


def _last_word_variants(word):
    variants = {word}
    lower_word = word.casefold()
    if len(word) < 3 or not re.fullmatch(r"[A-Za-z]+", word):
        return variants

    variants.add(f"{word}'s")

    if lower_word.endswith("y") and len(word) > 3:
        plural = f"{word[:-1]}ies"
    elif lower_word.endswith(("s", "x", "z")) or lower_word.endswith(("ch", "sh")):
        plural = f"{word}es"
    else:
        plural = f"{word}s"

    variants.add(plural)
    variants.add(f"{plural}'")
    return variants


def _alpha_frequency_surfaces(term):
    normalized = normalize_glossary_search_text(term).strip()
    if not normalized or LanguageDetector.is_cjk_text(normalized):
        return set()
    if not re.search(r"[A-Za-z]", normalized):
        return set()

    match = re.search(r"([A-Za-z]+)$", normalized)
    if not match:
        return {normalized}

    prefix = normalized[:match.start(1)]
    last_word = match.group(1)
    variants = {f"{prefix}{variant}" for variant in _last_word_variants(last_word)}

    if prefix.strip() and len(last_word) >= 4:
        variants.add(f"{prefix}{last_word}y")

    return variants


class GlossaryFrequencyVariantMatcher:
    """Counts safe alphabetic surface variants for frequency analysis only."""

    def __init__(self, glossary_terms):
        self.variant_map = defaultdict(set)
        self.pattern = None

        exact_keys = {
            _frequency_key(term)
            for term in glossary_terms or []
            if _frequency_key(term)
        }

        pattern_sources = set()
        for term in glossary_terms or []:
            base_key = _frequency_key(term)
            if not base_key:
                continue

            for surface in _alpha_frequency_surfaces(term):
                surface_key = _frequency_key(surface)
                if not surface_key:
                    continue
                if surface_key != base_key and surface_key in exact_keys:
                    continue

                self.variant_map[surface_key].add(term)
                pattern_sources.add(_surface_pattern(surface))

        if pattern_sources:
            sorted_sources = sorted(pattern_sources, key=len, reverse=True)
            self.pattern = re.compile(
                r"(?<![A-Za-z0-9_])(?:"
                + "|".join(sorted_sources)
                + r")(?![A-Za-z0-9_])",
                re.IGNORECASE,
            )

    def count_matches(self, text):
        found_counts = Counter()
        if not self.pattern:
            return found_counts

        normalized_text = normalize_glossary_search_text(text)
        for match in self.pattern.finditer(normalized_text):
            surface_key = _frequency_key(match.group(0))
            for original in self.variant_map.get(surface_key, ()):
                found_counts[original] += 1

        return found_counts


class GlossaryFrequencyWorker(QThread):
    """Фоновый анализатор частоты терминов по EPUB."""

    progress_update = pyqtSignal(int, int, str)
    analysis_finished = pyqtSignal(dict)
    error_occurred = pyqtSignal(str)

    def __init__(self, epub_path, glossary_data, parent=None):
        super().__init__(parent)
        self.epub_path = epub_path
        self.glossary_terms = collect_glossary_originals(glossary_data)
        self.glossary_dict = {term: {} for term in self.glossary_terms}
        self._is_running = True

    def run(self):
        try:
            if not self.epub_path or not os.path.exists(self.epub_path):
                self.error_occurred.emit(f"Файл не найден: {self.epub_path}")
                return

            if not self.glossary_terms:
                self.analysis_finished.emit(
                    build_term_frequency_payload(self.glossary_terms, self.epub_path, {})
                )
                return

            regex_service = GlossaryRegexService(self.glossary_dict)
            variant_matcher = GlossaryFrequencyVariantMatcher(self.glossary_terms)
            term_occurrences = Counter()
            term_distribution = defaultdict(set)

            with zipfile.ZipFile(self.epub_path, "r") as archive:
                html_files = [
                    name
                    for name in archive.namelist()
                    if name.lower().endswith((".html", ".xhtml", ".htm"))
                    and not name.startswith("__MACOSX")
                ]
                total_files = len(html_files)

                for index, filename in enumerate(html_files):
                    if not self._is_running:
                        break

                    self.progress_update.emit(index + 1, total_files, os.path.basename(filename))

                    try:
                        raw_content = archive.read(filename)
                        clean_text = _extract_text_from_html(raw_content)
                        match_counts = regex_service.count_matches(clean_text)
                        variant_counts = variant_matcher.count_matches(clean_text)
                        for term, count in variant_counts.items():
                            match_counts[term] = max(int(match_counts.get(term, 0) or 0), int(count or 0))

                        for term, count in match_counts.items():
                            count = int(count or 0)
                            if count <= 0:
                                continue
                            term_occurrences[term] += count
                            term_distribution[term].add(filename)
                    except Exception as exc:
                        print(f"[FreqAnalyzer] Ошибка чтения {filename}: {exc}")
                        continue

            if not self._is_running:
                return

            aggregated = aggregate_term_frequency_stats(
                self.glossary_terms,
                term_occurrences,
                term_distribution,
            )
            payload = build_term_frequency_payload(
                self.glossary_terms,
                self.epub_path,
                aggregated,
            )
            self.analysis_finished.emit(payload)
        except Exception as exc:
            self.error_occurred.emit(str(exc))

    def stop(self):
        self._is_running = False
