"""Свойства разбиения главы на чанки.

`split_text_into_chunks` режет главу на куски, которые переводятся порознь и
потом склеиваются обратно. Ошибка здесь не видна в примере и не ломает тест —
она молча теряет или задваивает кусок главы. Существующий
`test_split_text_chunks_no_overlap` проверяет ровно это, но на книжном тексте и
трёх наборах параметров; здесь то же самое требуется от произвольного входа при
произвольных target/window/min.

Настоящий контракт функции мягче, чем кажется, и оба послабления проверены на
единственном боевом вызывающем (`GlossaryTools._payloads_from_chunks`), который
к ним нечувствителен — при `len(chunks) <= 1` он откатывается на задачу «глава
целиком»:

* пустой вход возвращает `['']`, а не `[]`: ранний выход
  `if text_len <= target_size` стоит до фильтра пустых чанков;
* чанк из одних пробелов выбрасывается, поэтому склейка не обязана совпадать с
  исходником посимвольно — теряются только пробелы на стыке.

Поэтому здесь требуется сохранность непробельного содержимого и порядок, а не
побайтовое замощение.
"""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from gemini_translator.utils.text import split_text_into_chunks

_ALPHABET = "абвгдеёжзийклмнопрстуфхцчшщъыьэюя .,!?—«»\n\t<>/p"

# Боевые значения: api_config.chunk_search_window() и min_chunk_size() — 500.
# Диапазоны нарочно шире, чтобы задеть вырожденные сочетания.
targets = st.integers(min_value=50, max_value=4000)
windows = st.integers(min_value=0, max_value=1000)
min_sizes = st.integers(min_value=0, max_value=1000)


@st.composite
def chapter_html(draw):
    """Похожая на главу разметка: абзацы, заголовки, пустые блоки, голый текст."""
    blocks = []
    for _ in range(draw(st.integers(min_value=0, max_value=25))):
        kind = draw(st.integers(min_value=0, max_value=4))
        body = draw(st.text(alphabet=_ALPHABET, max_size=200))
        if kind == 0:
            blocks.append(f"<p>{body}</p>")
        elif kind == 1:
            blocks.append(f"<h2>{body}</h2>")
        elif kind == 2:
            blocks.append("<p></p>")
        elif kind == 3:
            blocks.append("\n\n" + " " * draw(st.integers(min_value=0, max_value=60)))
        else:
            blocks.append(body)
    return "".join(blocks)


source_text = st.one_of(
    st.text(alphabet=_ALPHABET, max_size=3000),
    chapter_html(),
)

PROPERTY_SETTINGS = settings(
    max_examples=120,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)


def _squeeze(value: str) -> str:
    """Текст без пробелов: именно он обязан пережить разбиение."""
    return "".join(value.split())


@PROPERTY_SETTINGS
@given(text=source_text, target=targets, window=windows, min_size=min_sizes)
def test_chunking_never_loses_or_duplicates_content(text, target, window, min_size):
    """Ни один символ главы не должен пропасть или повториться."""
    chunks = split_text_into_chunks(text, target, window, min_size)
    assert _squeeze("".join(chunks)) == _squeeze(text)


@PROPERTY_SETTINGS
@given(text=source_text, target=targets, window=windows, min_size=min_sizes)
def test_chunks_follow_the_original_order_without_overlap(text, target, window, min_size):
    """Регрессия utils-text/bugs/1, но уже на произвольном входе.

    Граница не имеет права откатываться назад: иначе следующий чанк заберёт
    кусок, уже отданный предыдущему, и в собранной главе появится задвоенный
    текст.
    """
    chunks = split_text_into_chunks(text, target, window, min_size)
    cursor = 0
    for index, chunk in enumerate(chunks):
        position = text.find(chunk, cursor)
        assert position >= cursor, (
            f"чанк {index} начинается раньше конца предыдущего "
            f"({position} < {cursor}); текст задвоится при сборке"
        )
        cursor = position + len(chunk)


@PROPERTY_SETTINGS
@given(text=source_text, target=targets, window=windows, min_size=min_sizes)
def test_only_the_degenerate_empty_input_yields_a_blank_chunk(text, target, window, min_size):
    """Пустые чанки допустимы ровно в одном вырожденном случае.

    Он безвреден: вызывающий при `len(chunks) <= 1` берёт главу целиком.
    Но если пустые чанки начнут появляться где-то ещё, это надо заметить.
    """
    chunks = split_text_into_chunks(text, target, window, min_size)
    blank = [chunk for chunk in chunks if not chunk.strip()]
    if blank:
        assert len(chunks) == 1, f"пустой чанк среди содержательных: {chunks!r}"
        assert not text.strip(), f"пустой чанк из непустого текста: {text!r}"


@PROPERTY_SETTINGS
@given(text=source_text, target=targets, window=windows, min_size=min_sizes)
def test_a_text_within_the_target_is_never_split(text, target, window, min_size):
    """Короткую главу резать не на что — она обязана уехать одним куском."""
    if len(text) <= target:
        assert split_text_into_chunks(text, target, window, min_size) == [text]
