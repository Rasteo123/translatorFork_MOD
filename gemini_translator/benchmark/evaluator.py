"""Heuristic quality checks for prompt/model benchmark runs."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from html.parser import HTMLParser
import json
import re
from typing import Any

try:
    from bs4 import BeautifulSoup
except Exception:  # pragma: no cover - the app normally depends on bs4
    BeautifulSoup = None


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


def visible_text(value: str) -> str:
    text = str(value or "")
    if BeautifulSoup is not None:
        soup = BeautifulSoup(text, "html.parser")
        text = soup.get_text(" ")
    else:
        text = re.sub(r"<[^>]+>", " ", text)
    return normalize_text(text)


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def estimate_tokens(value: str) -> int:
    text = str(value or "")
    if not text:
        return 0
    ascii_chars = len(re.findall(r"[\x00-\x7f]", text))
    cjk_chars = len(CJK_RE.findall(text))
    other_chars = max(0, len(text) - ascii_chars - cjk_chars)
    return int((ascii_chars / 4.0) + (cjk_chars / 1.5) + (other_chars / 2.3))


def extract_placeholders(value: str) -> list[str]:
    text = str(value or "")
    placeholders = []
    placeholders.extend(COMMENT_RE.findall(text))
    placeholders.extend(match.group(0) for match in TOKEN_PLACEHOLDER_RE.finditer(text))
    return sorted(dict.fromkeys(normalize_text(item) for item in placeholders if item.strip()))


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


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


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
    output_text = visible_text(output)
    reference_text = visible_text(reference)
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
    required_terms = [str(item) for item in _as_list(checks.get("required")) if str(item).strip()]
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

    forbidden_terms = [str(item) for item in _as_list(checks.get("forbidden")) if str(item).strip()]
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
    explicit_placeholders = {normalize_text(str(item)) for item in _as_list(checks.get("placeholders"))}
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

    allow_cjk = bool(checks.get("allow_cjk", False))
    cjk_count = len(CJK_RE.findall(visible_text(output_text)))
    metrics["cjk_residue_chars"] = cjk_count
    if cjk_count and not allow_cjk:
        score -= min(25.0, max(5.0, cjk_count * 2.0))
        issues.append(f"CJK residue chars: {cjk_count}")

    source_visible_len = len(visible_text(source_html))
    output_visible_len = len(visible_text(output_text))
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
    metrics["output_tokens_estimate"] = estimate_tokens(output_text)

    return BenchmarkEvaluation(max(0.0, min(100.0, score)), metrics, issues)
