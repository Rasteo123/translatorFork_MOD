"""Оборот главы через модель документа: разобрать и собрать обратно.

`build_html_document_model` раскладывает главу на узлы, перевод подставляется в
эти узлы, `render_document_html` собирает HTML обратно. Через эту пару проходит
каждая переведённая глава, и потеря здесь — это пропавший абзац в готовой
книге, который никакая валидация ниже уже не восстановит.

Свойства простые, но именно их отсутствие стоило бы дорого: оборот не теряет
видимый текст и приходит к неподвижной точке с первого раза.
"""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from gemini_translator.utils.epub_json import (
    build_html_document_model,
    render_document_html,
)
from gemini_translator.utils.html_text import extract_visible_text_normalized

_TEXT = st.text(alphabet="абвгдеёжзийклмн ,.!?—«»\n0123", max_size=60)
_TAGS = ["p", "h1", "h2", "h3", "em", "strong", "i", "b", "blockquote", "div", "span", "li"]


@st.composite
def chapter_document(draw):
    """Глава: блоки с текстом, вложенной разметкой и атрибутами."""
    parts = ["<html><body>"]
    for _ in range(draw(st.integers(min_value=0, max_value=8))):
        tag = draw(st.sampled_from(_TAGS))
        body = draw(_TEXT)
        kind = draw(st.integers(min_value=0, max_value=2))
        if kind == 0:
            parts.append(f"<{tag}>{body}</{tag}>")
        elif kind == 1:
            parts.append(f"<{tag}><em>{body}</em> {draw(_TEXT)}</{tag}>")
        else:
            parts.append(f'<{tag} class="x">{body}</{tag}>')
    parts.append("</body></html>")
    return "".join(parts)


PROPERTY_SETTINGS = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)


def _visible(value: str) -> str:
    """Видимый текст без пробелов: перенос строки при сборке значения не имеет."""
    return "".join(ch for ch in extract_visible_text_normalized(value) if not ch.isspace())


@PROPERTY_SETTINGS
@given(html=chapter_document())
def test_roundtrip_keeps_every_visible_character(html):
    """Разобрали и собрали — в главе не должно убыть ни буквы."""
    rendered = render_document_html(build_html_document_model(html))
    assert _visible(rendered) == _visible(html)


@PROPERTY_SETTINGS
@given(html=chapter_document())
def test_second_roundtrip_changes_nothing(html):
    """Повторная сборка обязана давать тот же байт-в-байт результат.

    Иначе повторная обработка главы (перевод, потом починка непереведённого)
    уводила бы разметку от прохода к проходу.
    """
    once = render_document_html(build_html_document_model(html))
    twice = render_document_html(build_html_document_model(once))
    assert twice == once
