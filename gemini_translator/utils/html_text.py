# -*- coding: utf-8 -*-
"""Быстрое извлечение видимого текста из HTML (selectolax/Lexbor, фолбэк BS4).

Только для read-only анализа: результат — строка, HTML из неё обратно не
сериализуется, поэтому семантика XHTML-сериализации BS4 здесь не нужна.
Семантика повторяет BeautifulSoup(html.parser): get_text(separator=' ',
strip=True) после удаления script/style/head/title/meta. Эквивалентность
закреплена корпусом tests/test_html_text_extraction.py.
"""

import re

from bs4 import BeautifulSoup

_EXCLUDED_TAGS = ('script', 'style', 'head', 'title', 'meta')
_EXCLUDED_CSS = ','.join(_EXCLUDED_TAGS)


def _bs4_extract(html_content: str) -> str:
    try:
        soup = BeautifulSoup(html_content, 'html.parser')
        for tag in soup(list(_EXCLUDED_TAGS)):
            tag.decompose()
        return soup.get_text(separator=' ', strip=True)
    except Exception:
        return html_content


try:
    from selectolax.lexbor import LexborHTMLParser

    HTML_TEXT_BACKEND = "selectolax"

    def extract_visible_text(html_content: str) -> str:
        try:
            tree = LexborHTMLParser(html_content)
            for node in tree.css(_EXCLUDED_CSS):
                node.decompose()
            # BS4 get_text(strip=True) пропускает узлы, пустые после strip;
            # selectolax оставил бы от них лишний сепаратор. Собираем через
            # NUL (в HTML-тексте он не выживает — Lexbor заменяет по спеке)
            # и отбрасываем пустые сегменты; пробелы ВНУТРИ узлов при этом
            # сохраняются, как у BS4.
            raw = tree.text(separator='\x00', strip=True)
            return ' '.join(segment for segment in raw.split('\x00') if segment)
        except Exception:
            # Lexbor не осилил вход — считаем тем же путём, что и раньше.
            return _bs4_extract(html_content)

except ImportError:
    HTML_TEXT_BACKEND = "beautifulsoup"
    extract_visible_text = _bs4_extract


def extract_visible_text_normalized(value, *, from_html: bool = True, limit: int | None = None) -> str:
    """extract_visible_text() + схлопывание пробелов (+опц. усечение до ``limit``).

    Канонический общий хелпер для мест, которым нужен не просто видимый
    текст, а ещё и «одна строка без переносов/двойных пробелов» — превью
    проблемных терминов, поиск по навигации, отпечаток главы и т. п. При
    ``from_html=False`` HTML не парсится вовсе (вход уже считается голым
    текстом) — только схлопывание пробелов и усечение.
    """
    if not value:
        return ""
    text = str(value)
    if from_html:
        text = extract_visible_text(text)
    text = re.sub(r'\s+', ' ', text).strip()
    if limit is not None and len(text) > limit:
        text = text[:limit].rstrip()
    return text
