"""Свойства отображения смещений при сплющивании блока.

`flatten_visible_text` склеивает видимый текст блока в одну строку и
возвращает карту `(id узла, начало, конец)` обратно в текстовые узлы. По этой
карте QA привязывает найденное замечание к конкретному узлу, а починка —
вписывает исправление на место.

Сдвиг на единицу здесь не падает и не логируется: замечание просто уезжает в
соседний узел, а правка встаёт не туда. Поэтому проверяется главное — срез
плоского текста по сегменту обязан совпадать с текстом того самого узла.
"""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from gemini_translator.qa.semantic_units import flatten_visible_text

_TEXT = st.text(alphabet="абвгдеж ,.!\n", max_size=25)


def _fragments(depth: int = 0):
    """Дерево фрагментов блока: текстовые узлы и вложенные элементы."""
    text_node = st.builds(
        lambda index, body: {"type": "text", "id": f"t{index}", "text": body},
        st.integers(min_value=0, max_value=999),
        _TEXT,
    )
    if depth >= 3:
        return st.lists(text_node, max_size=4)
    element = st.builds(
        lambda children: {"type": "element", "children": children},
        _fragments(depth + 1),
    )
    return st.lists(st.one_of(text_node, element), max_size=4)


PROPERTY_SETTINGS = settings(
    max_examples=150,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)


def _text_nodes(fragments, collected=None):
    collected = [] if collected is None else collected
    for fragment in fragments:
        if fragment["type"] == "text":
            collected.append((fragment["id"], fragment["text"]))
        else:
            _text_nodes(fragment["children"], collected)
    return collected


@PROPERTY_SETTINGS
@given(fragments=_fragments())
def test_each_segment_points_at_its_own_node(fragments):
    flat_text, segments = flatten_visible_text(fragments)
    nodes = [(node_id, body) for node_id, body in _text_nodes(fragments) if body]

    assert len(segments) == len(nodes), (
        f"сегментов {len(segments)}, непустых узлов {len(nodes)}"
    )
    for (segment_id, start, end), (node_id, body) in zip(segments, nodes):
        assert segment_id == node_id, f"перепутан узел: {segment_id} вместо {node_id}"
        assert flat_text[start:end] == body, (
            f"срез {flat_text[start:end]!r} не совпал с текстом узла {body!r}"
        )


@PROPERTY_SETTINGS
@given(fragments=_fragments())
def test_segments_run_forward_and_stay_inside_the_text(fragments):
    flat_text, segments = flatten_visible_text(fragments)
    previous_end = 0
    for _segment_id, start, end in segments:
        assert start >= previous_end, f"сегменты идут вспять: {start} < {previous_end}"
        assert start <= end <= len(flat_text), (
            f"границы вне плоского текста: {start}..{end} при len={len(flat_text)}"
        )
        previous_end = end
