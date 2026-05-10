from gemini_translator.utils.text import (
    coerce_translated_body_block,
    normalize_translated_body_wrapper,
)


def test_normalize_translated_body_wrapper_strips_xhtml_shell_without_body():
    original = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<html xmlns="http://www.w3.org/1999/xhtml">'
        '<head><title>Chapter</title></head>'
        '<body class="chapter"><h1>Chapter</h1><p>Source.</p></body>'
        '</html>'
    )
    translated = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<!DOCTYPE html>'
        '<html xmlns="http://www.w3.org/1999/xhtml">'
        '<head><title>Chapter</title></head>'
        '<h1>Glava</h1><p>Translated.</p>'
        '</html>'
    )

    repaired = normalize_translated_body_wrapper(original, translated)

    assert repaired == '<body class="chapter"><h1>Glava</h1><p>Translated.</p></body>'


def test_coerce_translated_body_block_extracts_body_from_full_document():
    original = '<html><head><title>Chapter</title></head><body id="main"><p>Source.</p></body></html>'
    translated = '<html><head><title>Chapter</title></head><body><p>Translated.</p></body></html>'

    repaired = coerce_translated_body_block(original, translated)

    assert repaired == '<body><p>Translated.</p></body>'
