# -*- coding: utf-8 -*-

import html as html_lib
import os
import re
from difflib import SequenceMatcher

try:
    from bs4 import Tag
except ImportError:  # pragma: no cover - duplicate analysis is only used with bs4 installed.
    Tag = ()


DUPLICATE_REVIEW_BLOCK_TAGS = (
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'p', 'div', 'blockquote', 'li',
    'section', 'article', 'aside',
)


def normalize_duplicate_text(text):
    text = html_lib.unescape(text or "")
    text = text.replace("\xa0", " ")
    text = text.casefold().replace("ё", "е")
    text = re.sub(r'[\W_]+', ' ', text, flags=re.UNICODE)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def is_meaningful_duplicate_text(normalized_text):
    if not normalized_text:
        return False
    compact = re.sub(r'[\W_]+', '', normalized_text, flags=re.UNICODE)
    return len(compact) >= 3


def get_duplicate_block_text(tag):
    return re.sub(r'\s+', ' ', " ".join(tag.stripped_strings)).strip()


def build_tag_path(root_tag, target_tag):
    path = []
    current = target_tag
    while current is not None and current is not root_tag:
        parent = current.parent
        if parent is None:
            return None
        child_tags = [child for child in parent.children if isinstance(child, Tag)]
        current_index = None
        for idx, child in enumerate(child_tags):
            if child is current:
                current_index = idx
                break
        if current_index is None:
            return None
        path.append(current_index)
        current = parent
    if current is not root_tag:
        return None
    path.reverse()
    return path


def resolve_tag_path(root_tag, path):
    current = root_tag
    for index in path:
        child_tags = [child for child in current.children if isinstance(child, Tag)]
        if index < 0 or index >= len(child_tags):
            return None
        current = child_tags[index]
    return current


def extract_duplicate_review_blocks(soup):
    root = soup.body or soup
    blocks = []
    for tag in root.find_all(DUPLICATE_REVIEW_BLOCK_TAGS):
        if tag.find_parent('svg'):
            continue
        if any(
            isinstance(child, Tag) and str(child.name).lower() in DUPLICATE_REVIEW_BLOCK_TAGS
            for child in tag.children
        ):
            continue

        text = get_duplicate_block_text(tag)
        normalized = normalize_duplicate_text(text)
        if not is_meaningful_duplicate_text(normalized):
            continue

        tag_path = build_tag_path(root, tag)
        if tag_path is None:
            continue

        blocks.append({
            'tag_name': tag.name.lower(),
            'tag_path': tag_path,
            'text': text,
            'norm_text': normalized,
        })
    return blocks


def format_duplicate_preview_blocks(blocks, selected_paths=None, keep_paths=None):
    selected_paths = {tuple(path) for path in (selected_paths or [])}
    keep_paths = {tuple(path) for path in (keep_paths or [])}
    lines = []
    for index, block in enumerate(blocks, start=1):
        path_key = tuple(block['tag_path'])
        marker = "   "
        if path_key in selected_paths:
            marker = "[x]"
        elif path_key in keep_paths:
            marker = "[=]"
        preview_text = block['text']
        if len(preview_text) > 180:
            preview_text = preview_text[:177] + "..."
        lines.append(f"{index:>2}. {marker} <{block['tag_name']}> {preview_text}")
    return "\n".join(lines)


def merge_finding_entry(store, key, finding):
    existing = store.get(key)
    if existing is None:
        finding['reasons'] = [finding['reason']]
        finding['preview_parts'] = [finding['preview']]
        store[key] = finding
        return

    if finding['reason'] not in existing['reasons']:
        existing['reasons'].append(finding['reason'])
    if finding['preview'] not in existing['preview_parts']:
        existing['preview_parts'].append(finding['preview'])
    existing['reason'] = "\n".join(existing['reasons'])
    existing['preview'] = "\n\n".join(existing['preview_parts'])


def summarize_duplicate_run(blocks):
    unique_texts = []
    for block in blocks:
        text = block.get('text', '').strip()
        if text and text not in unique_texts:
            unique_texts.append(text)

    if not unique_texts:
        return ""

    summary = " | ".join(unique_texts[:2])
    if len(unique_texts) > 2:
        summary += " ..."
    return summary


def get_duplicate_text_tokens(normalized_text):
    return [token for token in (normalized_text or "").split() if token]


def blocks_are_equivalent(left_block, right_block):
    if not left_block or not right_block:
        return False
    left_norm = (left_block.get('norm_text') or '').strip()
    right_norm = (right_block.get('norm_text') or '').strip()
    return bool(left_norm and left_norm == right_norm)


def blocks_look_like_same_heading(left_block, right_block):
    if not left_block or not right_block:
        return False

    left_norm = (left_block.get('norm_text') or '').strip()
    right_norm = (right_block.get('norm_text') or '').strip()
    if not left_norm or not right_norm:
        return False
    if left_norm == right_norm:
        return True

    max_len = max(len(left_norm), len(right_norm))
    if max_len > 220:
        return False

    left_tokens = set(get_duplicate_text_tokens(left_norm))
    right_tokens = set(get_duplicate_text_tokens(right_norm))
    if not left_tokens or not right_tokens:
        return False

    shared_tokens = left_tokens & right_tokens
    if not shared_tokens:
        return False

    overlap_ratio = len(shared_tokens) / max(1, min(len(left_tokens), len(right_tokens)))
    similarity_ratio = SequenceMatcher(None, left_norm, right_norm).ratio()

    left_numbers = {token for token in left_tokens if any(ch.isdigit() for ch in token)}
    right_numbers = {token for token in right_tokens if any(ch.isdigit() for ch in token)}
    if left_numbers and right_numbers and not (left_numbers & right_numbers):
        return False

    return (
        (overlap_ratio >= 0.80 and similarity_ratio >= 0.50) or
        (overlap_ratio >= 0.65 and similarity_ratio >= 0.62)
    )


def collect_start_duplicate_findings(chapter_infos):
    findings = []
    for info in chapter_infos:
        head_blocks = info['blocks'][:8]
        if len(head_blocks) < 2:
            continue

        keeper = head_blocks[0]
        keep_paths = [keeper['tag_path']]
        removable_blocks = []
        series_anchor = None
        for block in head_blocks[1:]:
            if block['tag_name'] == 'h1':
                break

            if not removable_blocks:
                if blocks_are_equivalent(keeper, block) or blocks_look_like_same_heading(keeper, block):
                    removable_blocks.append(block)
                    series_anchor = block
                    continue
                break

            previous_block = removable_blocks[-1]
            if (
                blocks_are_equivalent(previous_block, block) or
                blocks_are_equivalent(series_anchor, block) or
                blocks_are_equivalent(keeper, block) or
                blocks_look_like_same_heading(keeper, block)
            ):
                removable_blocks.append(block)
                continue
            break

        if not removable_blocks:
            continue

        preview = (
            f"Глава: {info['name']}\n"
            f"Сохраняется: <{keeper['tag_name']}> {keeper['text']}\n"
            f"Серия к удалению: {len(removable_blocks)} блок(ов) подряд\n\n"
            f"Первые строки главы:\n{format_duplicate_preview_blocks(head_blocks, [b['tag_path'] for b in removable_blocks], keep_paths)}"
        )
        findings.append({
            'category': 'start',
            'chapter_path': info['path'],
            'chapter_name': info['name'],
            'chapter_index': info['index'] + 1,
            'tag_name': removable_blocks[0]['tag_name'],
            'tag_paths': [list(block['tag_path']) for block in removable_blocks],
            'text': summarize_duplicate_run(removable_blocks),
            'block_count': len(removable_blocks),
            'location': "Начало главы",
            'reason': f"Будет удалена последовательная серия дублей в начале главы: {len(removable_blocks)} блок(ов).",
            'preview': preview,
        })

    return sorted(
        findings,
        key=lambda item: (item['chapter_index'], item['location'], item['text'].casefold())
    )


def collect_boundary_duplicate_findings(chapter_infos):
    findings = []

    for index in range(len(chapter_infos) - 1):
        current = chapter_infos[index]
        following = chapter_infos[index + 1]
        current_tail = current['blocks'][-4:]
        following_head_source = following['blocks']
        if following_head_source and following_head_source[0]['tag_name'] == 'h1':
            following_head_source = following_head_source[1:]
        following_head = following_head_source[:4]
        max_overlap = min(len(current_tail), len(following_head))
        overlap_size = 0

        for size in range(max_overlap, 0, -1):
            current_slice = current_tail[-size:]
            following_slice = following_head[:size]
            if [block['norm_text'] for block in current_slice] == [block['norm_text'] for block in following_slice]:
                overlap_size = size
                break

        if overlap_size <= 0:
            continue

        tail_overlap = current_tail[-overlap_size:]
        head_overlap = following_head[:overlap_size]
        removable_tail = [block for block in tail_overlap if block['tag_name'] != 'h1']
        removable_head = [block for block in head_overlap if block['tag_name'] != 'h1']

        if removable_tail:
            preview = (
                f"Совпадение между главами:\n"
                f"{current['name']} -> {following['name']}\n\n"
                f"Будет удалена серия с конца текущей главы: {len(removable_tail)} блок(ов)\n\n"
                f"Хвост текущей главы:\n{format_duplicate_preview_blocks(current_tail, [b['tag_path'] for b in removable_tail])}\n\n"
                f"Начало следующей главы:\n{format_duplicate_preview_blocks(following_head, [b['tag_path'] for b in head_overlap])}"
            )
            findings.append({
                'category': 'boundary',
                'chapter_path': current['path'],
                'chapter_name': current['name'],
                'chapter_index': current['index'] + 1,
                'tag_name': removable_tail[0]['tag_name'],
                'tag_paths': [list(block['tag_path']) for block in removable_tail],
                'text': summarize_duplicate_run(removable_tail),
                'block_count': len(removable_tail),
                'location': "Конец главы",
                'reason': f"Совпадает с началом следующей главы. Будет удалена серия из {len(removable_tail)} блок(ов).",
                'preview': preview,
            })

        if removable_head:
            preview = (
                f"Совпадение между главами:\n"
                f"{current['name']} -> {following['name']}\n\n"
                f"Будет удалена серия с начала следующей главы: {len(removable_head)} блок(ов)\n\n"
                f"Хвост предыдущей главы:\n{format_duplicate_preview_blocks(current_tail, [b['tag_path'] for b in tail_overlap])}\n\n"
                f"Начало текущей главы:\n{format_duplicate_preview_blocks(following_head, [b['tag_path'] for b in removable_head])}"
            )
            findings.append({
                'category': 'boundary',
                'chapter_path': following['path'],
                'chapter_name': following['name'],
                'chapter_index': following['index'] + 1,
                'tag_name': removable_head[0]['tag_name'],
                'tag_paths': [list(block['tag_path']) for block in removable_head],
                'text': summarize_duplicate_run(removable_head),
                'block_count': len(removable_head),
                'location': "Начало главы",
                'reason': f"Совпадает с концом предыдущей главы. Будет удалена серия из {len(removable_head)} блок(ов).",
                'preview': preview,
            })

    existing_paths = {
        (finding.get('chapter_path'), tuple(path))
        for finding in findings
        for path in (finding.get('tag_paths') or [])
        if finding.get('chapter_path') and path
    }

    ending_groups = {}
    for info in chapter_infos:
        for block in info['blocks'][-3:]:
            ending_groups.setdefault(block['norm_text'], []).append((info, block))

    for occurrences in ending_groups.values():
        chapter_paths = {info['path'] for info, _ in occurrences}
        if len(chapter_paths) < 2:
            continue

        chapter_names = ", ".join(sorted(os.path.basename(path) for path in chapter_paths))
        for info, block in occurrences:
            if block['tag_name'] == 'h1':
                continue

            block_key = (info['path'], tuple(block['tag_path']))
            if block_key in existing_paths:
                continue

            preview = (
                f"Глава: {info['name']}\n"
                f"Повторяющийся хвост: {block['text']}\n\n"
                f"Последние строки главы:\n{format_duplicate_preview_blocks(info['blocks'][-4:], [block['tag_path']])}\n\n"
                f"Также встречается в главах:\n{chapter_names}"
            )
            findings.append({
                'category': 'boundary',
                'chapter_path': info['path'],
                'chapter_name': info['name'],
                'chapter_index': info['index'] + 1,
                'tag_name': block['tag_name'],
                'tag_paths': [list(block['tag_path'])],
                'text': block['text'],
                'block_count': 1,
                'location': "Конец главы",
                'reason': f"Одинаковая концовка встречается в {len(chapter_paths)} главах.",
                'preview': preview,
            })
            existing_paths.add(block_key)

    return sorted(
        findings,
        key=lambda item: (item['chapter_index'], item['location'], item['text'].casefold())
    )


def analyze_duplicate_findings(chapter_infos):
    return {
        'start_findings': collect_start_duplicate_findings(chapter_infos),
        'boundary_findings': collect_boundary_duplicate_findings(chapter_infos),
    }
