"""Свойства разбора маркеров пакетного ответа.

Пакетный перевод возвращается одним текстом, разделённым маркерами
`<!-- N -->`; `find_boundary_markers` выбирает по ним цепочку границ, а
`response_parser` режет по ней главы. Ошибка тут не видна в примере: главы
просто приезжают перепутанными или пустыми.

Функция устроена как beam-поиск: из всех вхождений каждого маркера строится
цепочка, где каждый следующий стоит не раньше конца предыдущего, а из полных
цепочек выбирается та, где между границами больше кириллицы и содержания.
Смысл именно в этом — модель охотно повторяет маркеры внутри текста, и
наивный «последний выигрывает» разрезал бы главу не там.

Про неупорядоченный ответ. Если полной цепочки нет вовсе (например, модель
выдала `<!-- 1 --><!-- 0 -->`), функция возвращает карту всех вхождений, и та
может идти не по порядку. Это не дыра: `response_parser` берёт срез
`text[конец i : начало i+1]`, при перепутанном порядке получает пустую строку
и помечает главу как «Пустой контент (body)» — то есть она возвращается в
очередь, а не сохраняется испорченной. Последний тест фиксирует именно эту
безопасную деградацию.
"""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from gemini_translator.utils.batch_markers import find_boundary_markers

chapter_body = st.text(alphabet="абвгдежзийклмн ,.\n", min_size=5, max_size=60)

PROPERTY_SETTINGS = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)


@st.composite
def clean_batch_response(draw):
    """Честный ответ: маркеры 0..N ровно по одному разу и по порядку."""
    chapter_count = draw(st.integers(min_value=1, max_value=5))
    parts = []
    for marker_id in range(chapter_count + 1):
        parts.append(f"<!-- {marker_id} -->")
        parts.append(draw(chapter_body))
    return "".join(parts), chapter_count


@st.composite
def noisy_batch_response(draw):
    """Правильная цепочка на месте, но вокруг разбросаны лишние маркеры.

    Так выглядит реальный ответ: модель повторяет маркер внутри главы или
    дублирует его перед следующей.
    """
    chapter_count = draw(st.integers(min_value=1, max_value=4))
    parts = []
    for marker_id in range(chapter_count + 1):
        if draw(st.booleans()):
            stray = draw(st.integers(min_value=0, max_value=chapter_count))
            parts.append(f"<!-- {stray} -->")
            parts.append(draw(chapter_body))
        parts.append(f"<!-- {marker_id} -->")
        parts.append(draw(chapter_body))
    if draw(st.booleans()):
        parts.append(f"<!-- {draw(st.integers(min_value=0, max_value=chapter_count))} -->")
    return "".join(parts), chapter_count


def _chain(markers_map, chapter_count):
    return [markers_map[marker_id] for marker_id in range(chapter_count + 1)]


@PROPERTY_SETTINGS
@given(response=clean_batch_response())
def test_clean_response_yields_exactly_its_own_markers(response):
    text, chapter_count = response
    markers_map = find_boundary_markers(text, chapter_count=chapter_count)

    assert set(markers_map) >= set(range(chapter_count + 1))
    for marker_id, (start, end) in ((i, markers_map[i]) for i in range(chapter_count + 1)):
        assert text[start:end] == f"<!-- {marker_id} -->"


@PROPERTY_SETTINGS
@given(response=noisy_batch_response())
def test_the_ordered_chain_wins_over_stray_duplicates(response):
    """Ради этого и написан beam-поиск: шум не должен уводить границы."""
    text, chapter_count = response
    markers_map = find_boundary_markers(text, chapter_count=chapter_count)

    assert set(markers_map) >= set(range(chapter_count + 1)), (
        f"цепочка неполная: {sorted(markers_map)} при chapter_count={chapter_count}"
    )
    chain = _chain(markers_map, chapter_count)
    starts = [start for start, _end in chain]
    assert starts == sorted(starts), f"границы не по порядку: {starts}"
    for index in range(chapter_count):
        assert chain[index][1] <= chain[index + 1][0], (
            f"границы {index} и {index + 1} перекрываются: {chain}"
        )


@PROPERTY_SETTINGS
@given(response=st.one_of(clean_batch_response(), noisy_batch_response()))
def test_every_chapter_slice_is_non_empty_and_ordered(response):
    """Срез, который возьмёт response_parser, обязан быть содержательным."""
    text, chapter_count = response
    markers_map = find_boundary_markers(text, chapter_count=chapter_count)

    for index in range(chapter_count):
        start = markers_map[index][1]
        end = markers_map[index + 1][0]
        assert start <= end, f"глава {index}: срез вывернут ({start} > {end})"
        assert text[start:end].strip(), f"глава {index} вышла пустой"


def test_a_scrambled_response_degrades_to_an_empty_slice_not_to_wrong_text():
    """Перепутанный порядок обязан обнулить срез, а не подсунуть чужую главу.

    Пустой срез `response_parser` превращает в «Пустой контент (body)», и глава
    возвращается в очередь. Это единственная причина, по которой отсутствие
    гарантии порядка в запасном пути безопасно, — если поведение изменится,
    сломается именно этот тест.
    """
    text = "<!-- 1 -->Вторая глава<!-- 0 -->Первая глава"
    markers_map = find_boundary_markers(text, chapter_count=1)

    start = markers_map[0][1]
    end = markers_map[1][0]
    assert start > end, "порядок внезапно стал гарантированным — проверьте запасной путь"
    assert text[start:end] == "", "вывернутый срез обязан быть пустым, а не чужим текстом"
