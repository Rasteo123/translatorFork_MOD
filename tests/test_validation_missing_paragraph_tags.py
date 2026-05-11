from gemini_translator.ui.dialogs.validation import ValidationThread


def _worker():
    return ValidationThread(
        translated_folder="",
        original_epub_path="",
        checks_config={},
        word_exceptions_set=set(),
        project_manager=None,
    )


def test_validation_analysis_flags_unwrapped_body_text():
    result = {
        "path": "Text/chapter.xhtml",
        "internal_html_path": "Text/chapter.xhtml",
    }
    original = '<html><body><p>One.</p><p>Two.</p></body></html>'
    translated = '<html><body><p>One.</p>Loose text<p>Two.</p></body></html>'

    analyzed = _worker()._analyze_html_content(original, translated, result)

    assert analyzed["structural_errors"]["body_root_text"] == ["Loose text"]


def test_validation_analysis_allows_fewer_paragraph_tags_without_unwrapped_text():
    result = {
        "path": "Text/chapter.xhtml",
        "internal_html_path": "Text/chapter.xhtml",
    }
    original = (
        '<html><body>'
        '<p>One.</p><p>Two.</p><p>Three.</p><p>Four.</p>'
        '</body></html>'
    )
    translated = '<html><body><div>One. Two. Three. Four.</div></body></html>'

    analyzed = _worker()._analyze_html_content(original, translated, result)

    assert "structural_errors" not in analyzed
