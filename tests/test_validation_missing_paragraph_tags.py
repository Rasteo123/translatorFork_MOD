from gemini_translator.ui.dialogs.validation import ValidationThread


def test_validation_analysis_flags_missing_paragraph_tags():
    worker = ValidationThread(
        translated_folder="",
        original_epub_path="",
        checks_config={},
        word_exceptions_set=set(),
        project_manager=None,
    )
    result = {
        "path": "Text/chapter.xhtml",
        "internal_html_path": "Text/chapter.xhtml",
    }
    original = (
        '<html><body>'
        '<p>One.</p><p>Two.</p><p>Three.</p><p>Four.</p>'
        '</body></html>'
    )
    translated = '<html><body>One.\nTwo.\nThree.\nFour.</body></html>'

    analyzed = worker._analyze_html_content(original, translated, result)

    assert analyzed["structural_errors"]["missing_p_tags"] == (4, 0)
