"""Свойства преобразований типографики вместо очередных примеров.

В `utils/text.py` 79 функций, которые правят тире в диалогах, кавычки,
многоточия, пунктуацию и HTML от модели. Их покрывают 23 файла тестов, и все —
на конкретных строках. Примеры кончаются раньше, чем случаи: набор проверяет
то, что кто-то однажды придумал, и молчит про всё остальное.

Здесь проверяются два свойства, которые для нормализатора верны по замыслу.

1. Устойчивость. Текст в приложении проходит через эти функции не один раз:
   `initial_cleanup` при импорте, `finalize_cleanup` при сборке, `refine_...`
   по требованию. Если `f(f(x)) != f(x)`, повторная обработка уводит текст, и
   результат зависит от того, сколько раз файл открыли.
2. Отсутствие падений. На вход приходит вывод языковой модели, то есть что
   угодно: обрезанные теги, одиночные `<`, битые сущности, смесь письменностей.

Границы теста: генератор намеренно узкий — кириллица, латиница, цифры и та
пунктуация, ради которой эти функции написаны. Гонять сюда произвольный
Unicode смысла мало, суррогатные пары и приватные области в переводах не
встречаются.
"""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from gemini_translator.utils import text as text_utils
from gemini_translator.utils.html_text import extract_visible_text_normalized

# Алфавит подобран под задачу: то, что реально приезжает из перевода.
_LETTERS = "абвгдеёжзийклмнопрстуфхцчшщъыьэюяАБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯabcXYZ"
_DIGITS = "0123456789"
_PUNCTUATION = "—–- «»\"\"''…....,,!?:;()[]*_`"
_SPACES = " \n\t "
_CJK = "测试中文字符"
_HTML_CHARS = "<>&/=\"'"

TEXT_ALPHABET = _LETTERS + _DIGITS + _PUNCTUATION + _SPACES + _CJK + _HTML_CHARS

plain_text = st.text(alphabet=TEXT_ALPHABET, max_size=200)

_TAGS = ["p", "h1", "h2", "em", "strong", "i", "b", "blockquote", "div", "span"]


@st.composite
def html_fragment(draw):
    """Похожий на настоящий HTML кусок: несколько блоков с текстом внутри."""
    blocks = draw(st.lists(st.tuples(st.sampled_from(_TAGS), plain_text), max_size=4))
    return "".join(f"<{tag}>{body}</{tag}>" for tag, body in blocks)


# Функции, работающие с HTML-строкой.
HTML_TRANSFORMS = [
    "normalize_xhtml_tag_case",
    "normalize_dialogue_dashes",
    "normalize_chapter_heading_format",
    "oper_dash_symbol",
    "clean_glossary_garbage",
    "initial_cleanup",
    "refine_typography_in_html",
    "repair_unbalanced_paragraphs",
    "escape_stray_angle_brackets",
    "prettify_html_for_ai",
]

# Эти двое устойчивы не с первого прохода, а со второго: между блоками
# добавляется один перевод строки, и лишь потом схлопывание пустых строк
# приходит в равновесие. В HTML пробел между блочными элементами незначащий,
# так что пользователь разницы не видит, а гоняться за ней внутри 3800 строк
# регулярок дороже, чем она стоит. Требуем от них сходимости, а не мгновенной
# устойчивости, — и фиксируем, за сколько проходов.
CONVERGING_TRANSFORMS = ["prettify_html", "finalize_cleanup"]

# Функции, которые правят только пунктуацию и разметку: ни одна буква
# исчезнуть не должна. Сюда НЕ входят те, кто по замыслу выкидывает содержимое
# (clean_glossary_garbage) или пустые узлы (repair_unbalanced_paragraphs).
LETTER_PRESERVING_TRANSFORMS = [
    "normalize_xhtml_tag_case",
    "normalize_dialogue_dashes",
    "oper_dash_symbol",
    "refine_typography_in_html",
]

# Функции, работающие с обычным текстом.
PLAIN_TRANSFORMS = [
    "repair_quotes",
    "process_markdown_segment",
]

PROPERTY_SETTINGS = settings(
    max_examples=60,
    deadline=None,  # bs4 разбирает по-разному, дедлайн даёт ложные падения
    suppress_health_check=[HealthCheck.too_slow],
)


def _call(name, value):
    return getattr(text_utils, name)(value)


@pytest.mark.parametrize("name", HTML_TRANSFORMS + PLAIN_TRANSFORMS)
def test_transform_never_raises_on_realistic_junk(name):
    """На вывод языковой модели функция обязана отвечать, а не падать."""

    @PROPERTY_SETTINGS
    @given(value=st.one_of(plain_text, html_fragment()))
    def check(value):
        _call(name, value)

    check()


@pytest.mark.parametrize("name", HTML_TRANSFORMS)
def test_html_transform_is_idempotent(name):
    """Повторная обработка не должна двигать текст дальше."""

    @PROPERTY_SETTINGS
    @given(value=html_fragment())
    def check(value):
        once = _call(name, value)
        twice = _call(name, once)
        assert twice == once, (
            f"{name} неустойчив: второй проход меняет результат\n"
            f"вход:  {value!r}\nпосле 1: {once!r}\nпосле 2: {twice!r}"
        )

    check()


@pytest.mark.parametrize("name", PLAIN_TRANSFORMS)
def test_plain_transform_is_idempotent(name):
    @PROPERTY_SETTINGS
    @given(value=plain_text)
    def check(value):
        once = _call(name, value)
        twice = _call(name, once)
        assert twice == once, (
            f"{name} неустойчив: второй проход меняет результат\n"
            f"вход:  {value!r}\nпосле 1: {once!r}\nпосле 2: {twice!r}"
        )

    check()


@pytest.mark.parametrize("name", CONVERGING_TRANSFORMS)
def test_transform_reaches_a_fixed_point(name):
    """Пусть не с первого прохода, но повторная обработка обязана сходиться."""

    @PROPERTY_SETTINGS
    @given(value=html_fragment())
    def check(value):
        seen = []
        current = value
        for _ in range(4):
            current = _call(name, current)
            if seen and current == seen[-1]:
                return
            seen.append(current)
        raise AssertionError(
            f"{name} не сошёлся за 4 прохода\nвход: {value!r}\nпроходы: {seen!r}"
        )

    check()


def _letters_of_visible_text(html_value: str) -> str:
    """Буквы видимого текста, а не разметки.

    Считать по сырой строке нельзя: имена тегов — тоже буквы, а
    `escape_stray_angle_brackets` превращает одиночный `<` в `&lt;` и честно
    добавляет к строке `l` и `t`. Сравнивать надо то, что увидит читатель.
    """
    visible = extract_visible_text_normalized(html_value)
    return "".join(ch for ch in visible if ch.isalpha()).lower()


@pytest.mark.parametrize("name", LETTER_PRESERVING_TRANSFORMS)
def test_transform_never_eats_letters(name):
    """Правка пунктуации не должна терять буквы.

    Сравниваем без учёта регистра: часть функций умышленно ставит заглавную
    в начале предложения. А вот исчезнувшее слово — всегда ошибка.
    """

    @PROPERTY_SETTINGS
    @given(value=html_fragment())
    def check(value):
        result = _call(name, value)
        before = _letters_of_visible_text(value)
        after = _letters_of_visible_text(result)
        assert after == before, (
            f"{name} потерял буквы\nвход:  {value!r}\nвыход: {result!r}\n"
            f"было:  {before!r}\nстало: {after!r}"
        )

    check()


def test_escaping_stray_brackets_only_ever_recovers_text():
    """Экранирование одиночных `<` не теряет текст, а возвращает его.

    Строгое равенство букв тут неверно: bs4 съедает `<X` как незакрытый тег, и
    до экранирования этот текст читателю не виден вовсе. Функция для того и
    написана — значит букв может стать больше, но никогда меньше.
    """

    @PROPERTY_SETTINGS
    @given(value=st.one_of(plain_text, html_fragment()))
    def check(value):
        before = _letters_of_visible_text(value)
        after = _letters_of_visible_text(text_utils.escape_stray_angle_brackets(value))
        assert len(after) >= len(before), (
            f"экранирование потеряло текст\nвход:  {value!r}\n"
            f"было:  {before!r}\nстало: {after!r}"
        )

    check()
