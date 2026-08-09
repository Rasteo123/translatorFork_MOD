from gemini_translator.utils.text import (
    normalize_translated_body_wrapper,
    validate_html_structure,
)


def test_validate_html_structure_repairs_missing_open_body_wrapper():
    original = (
        '<body class="chapter">'
        '<h1>Chapter 165</h1>'
        '<p>First paragraph.</p>'
        '<p>Second paragraph.</p>'
        '</body>'
    )
    translated = (
        '<h1>Глава 165</h1>'
        '<p>Первый абзац.</p>'
        '<p>Второй абзац.</p>'
        '</body>'
    )

    is_valid, reason, repaired = validate_html_structure(original, translated)

    assert is_valid, reason
    assert repaired.startswith('<body class="chapter">')
    assert repaired.endswith('</body>')
    assert '<h1>Глава 165</h1>' in repaired


def test_validate_html_structure_wraps_leading_body_text_as_heading():
    original = (
        '<body class="chapter">'
        '<h1>Chapter 52</h1>'
        '<p>First paragraph.</p>'
        '</body>'
    )
    translated = (
        '52: Chapter 52 "OFFER"'
        '<p>Translated paragraph.</p>'
        '</body>'
    )

    is_valid, reason, repaired = validate_html_structure(original, translated)

    assert is_valid, reason
    assert repaired.startswith('<body class="chapter">')
    assert '<h1>52: Chapter 52 "OFFER"</h1>' in repaired
    assert '<p>Translated paragraph.</p>' in repaired


def test_validate_html_structure_normalizes_split_epub_heading_to_h1():
    original = (
        '<body class="chapter">'
        '<h2 class="head"><span class="chapter-sequence-number">'
        '\u7b2c175\u7ae0</span><br />\u5c0f\u59e8\u5230\u8bbf</h2>'
        '<p>First paragraph.</p>'
        '</body>'
    )
    translated = (
        '<body class="chapter">'
        '<h2>Глава 175: «Визит тети»</h2>'
        '<p>Первый абзац.</p>'
        '</body>'
    )

    is_valid, reason, repaired = validate_html_structure(original, translated)

    assert is_valid, reason
    assert '<h1>Глава 175: «Визит тети»</h1>' in repaired
    assert '<h2>Глава 175: «Визит тети»</h2>' not in repaired


def test_validate_html_structure_accepts_multi_h2_heading_collapsed_by_ai():
    original = (
        '<body class="chapter">'
        '<h2>Chapter 42</h2>'
        '<h2>Review: Hidden plot</h2>'
        '<p>First paragraph.</p>'
        '</body>'
    )
    translated = (
        '<body class="chapter">'
        '<h2>Glava 42 Review: Hidden plot</h2>'
        '<p>Translated paragraph.</p>'
        '</body>'
    )

    is_valid, reason, repaired = validate_html_structure(original, translated)

    assert is_valid, reason
    assert '<h1>Glava 42 Review: Hidden plot</h1>' in repaired
    assert '<h2>Glava 42 Review: Hidden plot</h2>' not in repaired


def test_validate_html_structure_accepts_multi_h2_heading_after_media_placeholder():
    original = (
        '<body class="chapter">'
        '<p><!-- MEDIA_0 --></p>'
        '<h2>Chapter 45</h2>'
        '<h2>End of Trial</h2>'
        '<p>First paragraph.</p>'
        '</body>'
    )
    translated = (
        '<body class="chapter">'
        '<p><!-- MEDIA_0 --></p>'
        '<h2>Chapter 45: End of Trial</h2>'
        '<p>Translated paragraph.</p>'
        '</body>'
    )

    is_valid, reason, repaired = validate_html_structure(original, translated)

    assert is_valid, reason
    assert '<p><!-- MEDIA_0 --></p>' in repaired
    assert '<h1>Chapter 45: End of Trial</h1>' in repaired
    assert '<h2>Chapter 45: End of Trial</h2>' not in repaired


def test_validate_html_structure_rejects_large_translation_missing_middle():
    source_paragraphs = [
        f"<p>Source paragraph {index}. " + ("source text " * 12) + "</p>"
        for index in range(40)
    ]
    translated_paragraphs = [
        "<p>" + ("\u041f\u0435\u0440\u0435\u0432\u043e\u0434 " * 10) + "</p>"
        for _ in range(5)
    ]
    original = "<body>" + "".join(source_paragraphs) + "</body>"
    translated = "<body>" + "".join(translated_paragraphs) + "</body>"

    is_valid, reason, _ = validate_html_structure(original, translated)

    assert not is_valid
    assert "too short" in reason


def test_validate_html_structure_rejects_large_paragraph_collapse():
    source_paragraphs = [
        f"<p>Source paragraph {index}. " + ("source text " * 12) + "</p>"
        for index in range(40)
    ]
    translated_text = "\u041f\u0435\u0440\u0435\u0432\u043e\u0434 " * 700
    original = "<body>" + "".join(source_paragraphs) + "</body>"
    translated = f"<body><p>{translated_text}</p></body>"

    is_valid, reason, _ = validate_html_structure(original, translated)

    assert not is_valid
    assert "collapsed or disappeared" in reason


def test_validate_html_structure_parses_each_unique_string_once(monkeypatch):
    import bs4

    from gemini_translator.utils import text as text_module

    parse_log = []
    real_soup = bs4.BeautifulSoup

    def counting_soup(markup="", *args, **kwargs):
        parse_log.append(str(markup)[:60])
        return real_soup(markup, *args, **kwargs)

    monkeypatch.setattr(bs4, "BeautifulSoup", counting_soup)
    monkeypatch.setattr(text_module, "BeautifulSoup", counting_soup)

    original = (
        '<body class="chapter">'
        '<h1>Chapter 7</h1>'
        + "".join(f"<p>Paragraph {index}.</p>" for index in range(6))
        + "</body>"
    )
    translated = (
        '<body class="chapter">'
        '<h1>Глава 7</h1>'
        + "".join(f"<p>Абзац {index}.</p>" for index in range(6))
        + "</body>"
    )

    is_valid, reason, _ = validate_html_structure(original, translated)

    assert is_valid, reason
    assert len(parse_log) <= 3, parse_log


def test_normalize_translated_body_wrapper_repairs_inner_html_response():
    original = '<body id="main"><p>Source text.</p></body>'
    translated = '<p>Переведенный текст.</p>'

    repaired = normalize_translated_body_wrapper(original, translated)

    assert repaired == '<body id="main"><p>Переведенный текст.</p></body>'
