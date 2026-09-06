"""Heuristic quality checks for prompt/model benchmark runs."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from html.parser import HTMLParser
import json
import re
from typing import Any

from ..utils.helpers import as_list, estimate_gemini_tokens
from ..utils.html_text import extract_visible_text_normalized

# dedup dups-gt_benchmark_evaluator-55: собственная копия estimate_tokens
# заменена каноническим utils/helpers.py::estimate_gemini_tokens; runner.py
# импортирует его оттуда же напрямую, реэкспорт-алиас здесь не нужен.
#
# extract_visible_text_normalized (utils/html_text.py) исключает <head>/<title>/<meta>
# из "видимого текста" — не только <script>/<style>. Старая
# evaluator.visible_text() гоняла BeautifulSoup.get_text() по всему
# документу и такой текст учитывала. Практическое следствие: непереведённый
# CJK-текст, оставшийся в XHTML-<title> главы, больше НЕ считается CJK
# residue и не влияет на cjk_residue_chars / output_visible_chars /
# length_ratio / reference_similarity (см. cjk_count и source_visible_len /
# output_visible_len ниже). Это осознанно принятое изменение семантики
# метрик бенчмарка при дедупе, а не баг — возврат прежнего сигнала
# потребовал бы параметра "исключаемые теги" в utils/html_text.py, а этот
# файл вне разрешённого периметра. Зафиксировано тестами
# test_evaluate_translation_cjk_in_title_is_not_flagged_as_residue и
# test_evaluator_module_documents_head_title_meta_exclusion.
#
# Тем же дедупом bs4 стал жёсткой зависимостью импорта этого модуля:
# utils/html_text.py делает безусловный `from bs4 import BeautifulSoup`,
# тогда как старый evaluator.py деградировал без bs4 до regex-очистки тегов
# (ветка была помечена `pragma: no cover`). beautifulsoup4 — обязательная
# зависимость (requirements.txt, requirements-translator-only.txt), поэтому
# отдельного фолбэка здесь не требуется.


CJK_RE = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\u3400-\u4dbf]")
COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
TOKEN_PLACEHOLDER_RE = re.compile(
    r"(\{\{[^{}\n]{1,120}\}\}|\[\[[^\[\]\n]{1,120}\]\]|__[A-Za-z][A-Za-z0-9_-]{0,80}__)"
)
VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


@dataclass
class BenchmarkEvaluation:
    score: float
    metrics: dict[str, Any] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(float(self.score), 2),
            "metrics": self.metrics,
            "issues": list(self.issues),
        }


class _TagCounter(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags = Counter()

    def handle_starttag(self, tag, attrs):
        self.tags[tag.lower()] += 1

    def handle_startendtag(self, tag, attrs):
        self.tags[tag.lower()] += 1


def extract_placeholders(value: str) -> list[str]:
    text = str(value or "")
    placeholders = []
    placeholders.extend(COMMENT_RE.findall(text))
    placeholders.extend(match.group(0) for match in TOKEN_PLACEHOLDER_RE.finditer(text))
    return sorted(
        dict.fromkeys(
            extract_visible_text_normalized(item, from_html=False)
            for item in placeholders
            if item.strip()
        )
    )


def extract_tag_counts(value: str) -> Counter:
    parser = _TagCounter()
    try:
        parser.feed(str(value or ""))
    except Exception:
        return Counter()
    return parser.tags


def _contains(haystack: str, needle: str, *, case_sensitive: bool = False) -> bool:
    if not needle:
        return True
    if case_sensitive:
        return needle in haystack
    return needle.casefold() in haystack.casefold()


def _glossary_required_terms(source_html: str, glossary_entries: list[dict[str, Any]]) -> list[str]:
    required = []
    for entry in glossary_entries:
        if not isinstance(entry, dict):
            continue
        original = str(entry.get("original") or entry.get("source") or "").strip()
        translated = str(entry.get("rus") or entry.get("translation") or entry.get("target") or "").strip()
        if original and translated and original in source_html:
            required.append(translated)
    return required


def _reference_similarity(output: str, reference: str) -> float | None:
    if not reference:
        return None
    output_text = extract_visible_text_normalized(output)
    reference_text = extract_visible_text_normalized(reference)
    if not output_text or not reference_text:
        return 0.0
    return SequenceMatcher(None, output_text.casefold(), reference_text.casefold()).ratio()


def evaluate_translation(
    source_html: str,
    output_text: str,
    *,
    reference_text: str = "",
    glossary_entries: list[dict[str, Any]] | None = None,
    checks: dict[str, Any] | None = None,
) -> BenchmarkEvaluation:
    """Evaluate one model response with deterministic checks.

    This is intentionally heuristic. It catches regressions that matter for this
    translator: lost markup/placeholders, untranslated CJK residue, glossary
    misses, and obviously wrong output length.
    """

    source_html = str(source_html or "")
    output_text = str(output_text or "")
    glossary_entries = list(glossary_entries or [])
    checks = dict(checks or {})
    score = 100.0
    issues: list[str] = []
    metrics: dict[str, Any] = {}

    case_sensitive = bool(checks.get("case_sensitive", False))
    required_terms = [str(item) for item in as_list(checks.get("required")) if str(item).strip()]
    if checks.get("glossary_required", True):
        required_terms.extend(_glossary_required_terms(source_html, glossary_entries))
    required_terms = sorted(dict.fromkeys(required_terms))

    missing_required = [
        term for term in required_terms if not _contains(output_text, term, case_sensitive=case_sensitive)
    ]
    if missing_required:
        score -= min(45.0, 15.0 * len(missing_required))
        issues.append("missing required terms: " + ", ".join(missing_required[:8]))
    metrics["required_terms"] = {
        "total": len(required_terms),
        "missing": missing_required,
    }

    forbidden_terms = [str(item) for item in as_list(checks.get("forbidden")) if str(item).strip()]
    found_forbidden = [
        term for term in forbidden_terms if _contains(output_text, term, case_sensitive=case_sensitive)
    ]
    if found_forbidden:
        score -= min(35.0, 10.0 * len(found_forbidden))
        issues.append("found forbidden terms: " + ", ".join(found_forbidden[:8]))
    metrics["forbidden_terms"] = {
        "total": len(forbidden_terms),
        "found": found_forbidden,
    }

    source_placeholders = set(extract_placeholders(source_html))
    explicit_placeholders = {
        extract_visible_text_normalized(str(item), from_html=False) for item in as_list(checks.get("placeholders"))
    }
    placeholders = sorted(item for item in (source_placeholders | explicit_placeholders) if item)
    missing_placeholders = [item for item in placeholders if item not in output_text]
    if missing_placeholders:
        score -= min(35.0, 10.0 * len(missing_placeholders))
        issues.append("missing placeholders: " + ", ".join(missing_placeholders[:8]))
    metrics["placeholders"] = {
        "total": len(placeholders),
        "missing": missing_placeholders,
    }

    if checks.get("preserve_html_tags", True):
        source_tags = extract_tag_counts(source_html)
        output_tags = extract_tag_counts(output_text)
        diff = {}
        tag_diff_count = 0
        for tag in sorted(set(source_tags) | set(output_tags)):
            if tag in VOID_TAGS and checks.get("ignore_void_tag_diff", True):
                continue
            delta = output_tags.get(tag, 0) - source_tags.get(tag, 0)
            if delta:
                diff[tag] = delta
                tag_diff_count += abs(delta)
        if tag_diff_count:
            score -= min(25.0, 5.0 * tag_diff_count)
            issues.append("html tag count changed: " + json.dumps(diff, ensure_ascii=False, sort_keys=True))
        metrics["html_tag_diff"] = diff

    # <head>/<title>/<meta> вне "видимого текста" (см. комментарий у импортов
    # выше) — CJK внутри <title> сюда не попадёт.
    allow_cjk = bool(checks.get("allow_cjk", False))
    cjk_count = len(CJK_RE.findall(extract_visible_text_normalized(output_text)))
    metrics["cjk_residue_chars"] = cjk_count
    if cjk_count and not allow_cjk:
        score -= min(25.0, max(5.0, cjk_count * 2.0))
        issues.append(f"CJK residue chars: {cjk_count}")

    source_visible_len = len(extract_visible_text_normalized(source_html))
    output_visible_len = len(extract_visible_text_normalized(output_text))
    length_ratio = (output_visible_len / source_visible_len) if source_visible_len else None
    metrics["length_ratio"] = round(length_ratio, 3) if length_ratio is not None else None
    min_ratio = float(checks.get("min_length_ratio", 0.25))
    max_ratio = float(checks.get("max_length_ratio", 4.0))
    if length_ratio is not None and output_visible_len:
        if length_ratio < min_ratio or length_ratio > max_ratio:
            score -= 10.0
            issues.append(f"length ratio out of range: {length_ratio:.3f}")

    if checks.get("expect_json", False):
        try:
            json.loads(output_text)
            metrics["json_valid"] = True
        except Exception as exc:
            metrics["json_valid"] = False
            score -= 20.0
            issues.append(f"invalid JSON output: {type(exc).__name__}")

    similarity = _reference_similarity(output_text, reference_text)
    metrics["reference_similarity"] = round(similarity, 4) if similarity is not None else None
    if similarity is not None:
        min_similarity = float(checks.get("min_similarity", 0.0) or 0.0)
        if min_similarity > 0 and similarity < min_similarity:
            penalty = min(25.0, ((min_similarity - similarity) / max(min_similarity, 0.01)) * 25.0)
            score -= penalty
            issues.append(f"reference similarity below {min_similarity:.2f}: {similarity:.3f}")

    metrics["source_visible_chars"] = source_visible_len
    metrics["output_visible_chars"] = output_visible_len
    metrics["output_tokens_estimate"] = estimate_gemini_tokens(output_text)

    return BenchmarkEvaluation(max(0.0, min(100.0, score)), metrics, issues)
