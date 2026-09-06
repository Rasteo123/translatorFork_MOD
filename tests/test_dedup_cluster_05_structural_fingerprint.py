"""cluster-05: _create_structural_fingerprint дублировалась байт-в-байт между
gemini_translator/ui/dialogs/validation.py (ValidationThread._create_structural_fingerprint)
и gemini_translator/utils/text.py (свободная функция).

Каноническая реализация — utils/text._create_structural_fingerprint(soup).
ValidationThread должен использовать её напрямую, без собственной копии метода.
"""

from bs4 import BeautifulSoup

from gemini_translator.utils.text import _create_structural_fingerprint
from gemini_translator.ui.dialogs import validation as validation_module
from gemini_translator.ui.dialogs.validation import ValidationThread


def _worker():
    return ValidationThread(
        translated_folder="",
        original_epub_path="",
        checks_config={},
        word_exceptions_set=set(),
        project_manager=None,
    )


# --- (a) Характеризационные тесты канонической реализации ---

def test_fingerprint_counts_images_links_lists():
    soup = BeautifulSoup(
        '<html><body>'
        '<img src="a.png"><img src="b.png">'
        '<a href="x">x</a>'
        '<ul><li>1</li></ul><ol><li>2</li></ol>'
        '</body></html>',
        'html.parser',
    )
    fp = _create_structural_fingerprint(soup)
    assert fp['images'] == 2
    assert fp['links'] == 1
    assert fp['lists'] == 2


def test_fingerprint_counts_headings_by_level():
    soup = BeautifulSoup(
        '<html><body><h1>A</h1><h2>B</h2><h2>C</h2><h3>D</h3></body></html>',
        'html.parser',
    )
    fp = _create_structural_fingerprint(soup)
    assert fp['headings'] == {'h1': 1, 'h2': 2, 'h3': 1}


def test_fingerprint_empty_soup_has_zeroed_counts_and_empty_headings():
    soup = BeautifulSoup('<html><body></body></html>', 'html.parser')
    fp = _create_structural_fingerprint(soup)
    assert fp == {'headings': {}, 'images': 0, 'links': 0, 'lists': 0}


# --- (b) Маршрутизация: ValidationThread._analyze_html_content должен идти
# через каноническую utils.text._create_structural_fingerprint, а не через
# собственную копию. До рефакторинга этот тест ПАДАЕТ (в validation.py своя
# копия метода, monkeypatch модуля-канона её не перехватывает).

def test_analyze_html_content_routes_through_canonical_fingerprint(monkeypatch):
    calls = []
    sentinel = {'headings': {}, 'images': 0, 'links': 0, 'lists': 0}

    def fake_fingerprint(soup):
        calls.append(soup)
        return dict(sentinel)

    monkeypatch.setattr(validation_module, '_create_structural_fingerprint', fake_fingerprint)

    result = {
        "path": "Text/chapter.xhtml",
        "internal_html_path": "Text/chapter.xhtml",
    }
    original = '<html><body><p>One.</p></body></html>'
    translated = '<html><body><p>One.</p></body></html>'

    _worker()._analyze_html_content(original, translated, result)

    assert len(calls) == 2, "ValidationThread must call the canonical module-level fingerprint helper"


def test_validation_thread_has_no_own_fingerprint_method():
    assert not hasattr(ValidationThread, '_create_structural_fingerprint'), (
        "ValidationThread must not keep its own duplicate of _create_structural_fingerprint"
    )
