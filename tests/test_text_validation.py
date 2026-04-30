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


def test_normalize_translated_body_wrapper_repairs_inner_html_response():
    original = '<body id="main"><p>Source text.</p></body>'
    translated = '<p>Переведенный текст.</p>'

    repaired = normalize_translated_body_wrapper(original, translated)

    assert repaired == '<body id="main"><p>Переведенный текст.</p></body>'
