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


def test_validate_html_structure_rejects_unrepairable_lost_paragraph_tags():
    original = (
        '<body>'
        '<p>One.</p><p>Two.</p><p>Three.</p><p>Four.</p>'
        '</body>'
    )
    translated = '<body><div>One. Two. Three. Four.</div></body>'

    is_valid, reason, repaired = validate_html_structure(original, translated)

    assert not is_valid
    assert 'Потеряны теги <p>' in reason


def test_validate_html_structure_rejects_merged_paragraph_count_after_repair():
    original = '<body><p>One.</p><p>Two.</p></body>'
    translated = '<body>One. Two.</body>'

    is_valid, reason, repaired = validate_html_structure(original, translated)

    assert not is_valid
    assert 'Потеряны теги <p>' in reason
