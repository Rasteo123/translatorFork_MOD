from bs4 import BeautifulSoup, NavigableString, Tag
from bs4.element import PreformattedString

from gemini_translator.api.errors import PartialGenerationError, QrouteOutputLimitError, ValidationFailedError
from gemini_translator.utils.text import (
    is_content_effectively_empty,
    process_body_tag,
    split_text_into_chunks,
)


def _text_size(node):
    if isinstance(node, Tag):
        return len(node.get_text(strip=True))
    if isinstance(node, PreformattedString):
        return 0
    return len(node.strip())


def _render_node(node):
    return node.output_ready(formatter="minimal") if isinstance(node, NavigableString) else str(node)


def _edge_whitespace(text):
    return text[:len(text) - len(text.lstrip())], text[len(text.rstrip()):]


def _split_fragment(source):
    """Split between intact HTML nodes, or split a single text node as text."""
    prefix, body, suffix = process_body_tag(source, return_parts=True, body_content_only=True)
    document = BeautifulSoup(body, "html.parser")
    container = document
    while True:
        nodes = list(container.contents)
        visible = [(index, size) for index, node in enumerate(nodes) if (size := _text_size(node))]
        if len(visible) != 1 or not isinstance(nodes[visible[0][0]], Tag):
            break
        container = nodes[visible[0][0]]

    def render():
        return prefix + document.decode_contents() + suffix

    if not visible:
        return None
    if len(visible) == 1:
        node = nodes[visible[0][0]]
        original_text = str(node)
        plain = original_text.strip()
        if len(plain) < 2:
            return None
        parts = split_text_into_chunks(plain, (len(plain) + 1) // 2, len(plain) // 10, 1)
        if len(parts) < 2:
            return None

        def join_text(translations):
            left, right = parts
            separator = _edge_whitespace(left)[1] + _edge_whitespace(right)[0] or " "
            before, after = _edge_whitespace(original_text)
            if container is document:
                fragment = BeautifulSoup(before + separator.join(translations) + after, "html.parser")
                node.replace_with(*fragment.contents)
            else:
                text = separator.join(BeautifulSoup(part, "html.parser").get_text(" ", strip=True) for part in translations)
                node.replace_with(NavigableString(before + text + after))
            return render()

        return [_render_node(NavigableString(part)) for part in parts], join_text

    total_size = sum(size for _, size in visible)
    left_size = 0
    boundaries = []
    for (_, size), (next_index, _) in zip(visible, visible[1:]):
        left_size += size
        boundaries.append((abs(total_size - 2 * left_size), next_index))
    _, split_at = min(boundaries)
    parts = ["".join(map(_render_node, part)) for part in (nodes[:split_at], nodes[split_at:])]

    def join_html(translations):
        container.clear()
        for source_part, translated_part in zip(parts, translations, strict=True):
            before, after = _edge_whitespace(source_part)
            fragment = BeautifulSoup(before + translated_part + after, "html.parser")
            container.extend(fragment.contents)
        return render()

    return parts, join_html


async def translate_split_fragment(worker, source, error, translate):
    """Recover a clipped translation using independent, smaller requests."""
    split = _split_fragment(source)
    if split is None:
        raise error
    parts, join = split
    worker._post_event("log_message", {
        "message": f"Qroute достиг лимита токенов. Переводим фрагмент по частям ({len(parts)}).",
    })
    translations = []
    for part in parts:
        try:
            translated = await translate(part, error.provider_attempt)
        except QrouteOutputLimitError as exc:
            translated = await translate_split_fragment(worker, part, exc, translate)
        except PartialGenerationError as exc:
            # Only a prefix of the parent can be used for continuation.
            exc.partial_text = ""
            raise
        if is_content_effectively_empty(translated):
            raise ValidationFailedError("Qroute вернул пустой перевод части фрагмента.")
        translations.append(process_body_tag(translated))
    return join(translations)
