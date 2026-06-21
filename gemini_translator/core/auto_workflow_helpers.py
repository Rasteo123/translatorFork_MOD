from __future__ import annotations

import os
import re
from typing import Any


AUTO_CJK_SHORT_RATIO_LIMIT = 1.80
AUTO_CJK_CHAR_RE = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")


def _extract_number_from_path(path: Any):
    filename = os.path.basename(str(path or ""))
    match = re.search(r"(\d+)", filename)
    if match:
        try:
            return int(match.group(1))
        except (ValueError, IndexError):
            return float("inf")
    return float("inf")


def text_has_cjk(text: Any) -> bool:
    return isinstance(text, str) and bool(AUTO_CJK_CHAR_RE.search(text))


def auto_result_uses_cjk_ratio(result_data: Any, chapter_has_cjk=None) -> bool:
    if not isinstance(result_data, dict):
        return False

    if result_data.get("is_cjk_original") is True:
        return True

    for field_name in ("original_html", "original_text", "original_content"):
        if text_has_cjk(result_data.get(field_name)):
            return True

    if chapter_has_cjk:
        return bool(chapter_has_cjk(result_data.get("internal_html_path")))
    return False


def effective_auto_short_ratio_limit(
    auto_settings: Any,
    result_data: Any = None,
    chapter_has_cjk=None,
) -> tuple[float, str]:
    if not isinstance(auto_settings, dict):
        auto_settings = {}
    if not isinstance(result_data, dict):
        result_data = {}

    base_limit = float(auto_settings.get("retry_short_ratio", 0.70) or 0.70)
    if auto_result_uses_cjk_ratio(result_data, chapter_has_cjk=chapter_has_cjk):
        return max(base_limit, AUTO_CJK_SHORT_RATIO_LIMIT), "CJK"
    return base_limit, "alphabetic"


def estimate_auto_task_size_limit(token_limit: Any) -> tuple[int | None, str | None]:
    token_limit = int(token_limit or 0)
    if token_limit <= 0:
        return None, None

    profile_name = "Gemini-токены"
    task_token_limit = max(500, min(token_limit, 350000))
    return task_token_limit, profile_name


def build_sequential_chapter_chains(chapters: Any, split_count: Any) -> list[list[str]]:
    chapters = list(chapters or [])
    if not chapters:
        return []
    try:
        split_count = int(split_count)
    except (TypeError, ValueError):
        split_count = 1
    split_count = max(1, min(split_count, len(chapters)))

    chains = []
    for index in range(split_count):
        start = (index * len(chapters)) // split_count
        end = ((index + 1) * len(chapters)) // split_count
        if start < end:
            chains.append(chapters[start:end])
    return chains


def extract_chapters_from_payload(payload: Any) -> list[str]:
    if not payload:
        return []

    task_type = payload[0]
    if task_type in ("epub", "epub_chunk") and len(payload) > 2:
        return [payload[2]]
    if task_type == "epub_batch" and len(payload) > 2:
        return list(payload[2])
    return []


def normalize_auto_chapters(chapters: Any, preserve_order: bool = False) -> list[str]:
    if not chapters:
        return []

    normalized = []
    seen = set()
    for chapter in chapters:
        if not isinstance(chapter, str) or not chapter:
            continue
        if chapter in seen:
            continue
        seen.add(chapter)
        normalized.append(chapter)

    if preserve_order:
        return normalized
    return sorted(normalized, key=_extract_number_from_path)


def make_auto_chapter_signature(chapters: Any) -> tuple[str, ...]:
    return tuple(normalize_auto_chapters(chapters, preserve_order=False))


def short_auto_name(chapter: str, max_length: int = 84) -> str:
    text = os.path.basename(chapter) if isinstance(chapter, str) else str(chapter)
    if len(text) <= max_length:
        return text
    return text[: max_length - 1] + "…"


def format_auto_chapter_list(chapters: Any, limit: int = 8, preserve_order: bool = False) -> str:
    normalized = normalize_auto_chapters(chapters, preserve_order=preserve_order)
    if not normalized:
        return "нет глав"

    display_items = [short_auto_name(chapter) for chapter in normalized[:limit]]
    if len(normalized) > limit:
        display_items.append(f"… +{len(normalized) - limit}")
    return ", ".join(display_items)


def compose_auto_details(sections: Any) -> str:
    blocks = []
    for title, content in sections:
        text = ""
        if isinstance(content, str):
            text = content.strip()
        elif isinstance(content, (list, tuple)):
            lines = [str(item).strip() for item in content if str(item).strip()]
            text = "\n".join(f"- {line}" for line in lines)
        elif isinstance(content, set):
            lines = [
                str(item).strip()
                for item in sorted(content, key=_extract_number_from_path)
                if str(item).strip()
            ]
            text = "\n".join(f"- {line}" for line in lines)
        if not text:
            continue
        if title:
            blocks.append(f"{title}:\n{text}")
        else:
            blocks.append(text)
    return "\n\n".join(blocks)


def truncate_auto_trace_text(text: str | None, limit: int = 4000) -> str:
    normalized = str(text or "").strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 16)].rstrip() + "\n...[truncated]..."


def merge_auto_details(*parts: str) -> str:
    return "\n\n".join(
        str(part).strip()
        for part in parts
        if isinstance(part, str) and part.strip()
    )


def compose_auto_trace_details(traces: Any, max_entries: int = 4, text_limit: int = 4000) -> str:
    phase_titles = {
        "glossary_collection": "Glossary collection",
        "analysis": "Analysis",
        "fix": "Fix",
    }
    trace_items = [trace for trace in (traces or []) if isinstance(trace, dict)]
    total = len(trace_items)
    if not total:
        return ""

    blocks = []
    for index, trace in enumerate(trace_items[:max_entries], start=1):
        phase_name = phase_titles.get(trace.get("phase"), str(trace.get("phase") or "trace"))
        header = f"[{index}/{total}] {phase_name}"
        chapter_names = [
            str(name).strip()
            for name in (trace.get("chapter_names") or [])
            if str(name).strip()
        ]
        if chapter_names:
            header += f" · {format_auto_chapter_list(chapter_names, limit=4, preserve_order=True)}"

        metadata = trace.get("metadata") if isinstance(trace.get("metadata"), dict) else {}
        metadata_lines = []
        for key in ("chunk_index", "total_chunks", "mode", "problem_count", "batch_mode"):
            value = metadata.get(key)
            if value is None:
                continue
            metadata_lines.append(f"{key}: {value}")

        entry_parts = [header]
        if metadata_lines:
            entry_parts.append("Метаданные:\n" + "\n".join(f"- {line}" for line in metadata_lines))

        prompt_text = truncate_auto_trace_text(trace.get("prompt"), text_limit)
        if prompt_text:
            entry_parts.append(f"Запрос:\n{prompt_text}")

        response_text = truncate_auto_trace_text(trace.get("response"), text_limit)
        if response_text:
            entry_parts.append(f"Ответ:\n{response_text}")

        blocks.append("\n\n".join(entry_parts))

    if total > max_entries:
        blocks.append(f"... скрыто трассировок: {total - max_entries}")

    return "\n\n".join(blocks)


def describe_auto_payload(payload: Any) -> str:
    chapters = extract_chapters_from_payload(payload)
    task_type = payload[0] if payload else "unknown"

    if task_type == "epub_batch":
        return (
            f"пакет {len(chapters)} глав: "
            f"{format_auto_chapter_list(chapters, limit=5, preserve_order=True)}"
        )
    if task_type == "epub_chunk":
        return (
            "чанк: "
            f"{format_auto_chapter_list(chapters, limit=3, preserve_order=True)}"
        )
    if task_type == "epub":
        return (
            "глава: "
            f"{format_auto_chapter_list(chapters, limit=1, preserve_order=True)}"
        )
    return (
        f"{task_type}: "
        f"{format_auto_chapter_list(chapters, limit=4, preserve_order=True)}"
    )
