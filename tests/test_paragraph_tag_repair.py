from gemini_translator.utils.text import validate_html_structure


def test_validate_html_structure_repairs_flattened_root_paragraph_text():
    original = (
        '<body class="chapter">'
        '<h1>Chapter 1</h1>'
        '<p>First paragraph.</p>'
        '<p>Second paragraph.</p>'
        '</body>'
    )
    translated = (
        '<body class="chapter">'
        '<h1>Glava 1</h1>'
        'First translated paragraph.\n'
        'Second translated paragraph.'
        '</body>'
    )

    is_valid, reason, repaired = validate_html_structure(original, translated)

    assert is_valid, reason
    assert '<p>First translated paragraph.</p>' in repaired
    assert '<p>Second translated paragraph.</p>' in repaired


def test_validate_html_structure_allows_fewer_paragraph_tags_inside_block():
    original = (
        '<body>'
        '<p>One.</p><p>Two.</p><p>Three.</p><p>Four.</p>'
        '</body>'
    )
    translated = '<body><div>One. Two. Three. Four.</div></body>'

    is_valid, reason, repaired = validate_html_structure(original, translated)

    assert is_valid, reason
    assert '<div>One. Two. Three. Four.</div>' in repaired


def test_validate_html_structure_repairs_merged_root_text_to_single_paragraph():
    original = '<body><p>One.</p><p>Two.</p></body>'
    translated = '<body>One. Two.</body>'

    is_valid, reason, repaired = validate_html_structure(original, translated)

    assert is_valid, reason
    assert repaired == '<body><p>One. Two.</p></body>'
