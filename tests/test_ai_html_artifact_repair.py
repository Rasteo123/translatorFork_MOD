from gemini_translator.ui.dialogs.validation import ValidationThread
from gemini_translator.utils.text import (
    escape_stray_angle_brackets,
    find_stray_angle_bracket_snippets,
    find_unwrapped_body_text_snippets,
    is_well_formed_xml,
    repair_ai_html_artifacts,
)


def _worker():
    return ValidationThread(
        translated_folder="",
        original_epub_path="",
        checks_config={},
        word_exceptions_set=set(),
        project_manager=None,
    )


def test_escape_stray_angle_brackets_preserves_real_tags():
    html = '<body><p>2 < 3 and 5 > 4</p><a href="notes.xhtml#n1">note</a></body>'

    repaired = escape_stray_angle_brackets(html)

    assert '<body><p>' in repaired
    assert '<a href="notes.xhtml#n1">note</a>' in repaired
    assert '2 &lt; 3 and 5 &gt; 4' in repaired


def test_repair_ai_html_artifacts_wraps_body_text_and_escapes_angles():
    original = '<html><body><p>One.</p><p>Two.</p></body></html>'
    translated = '<html><body><p>One < stray ></p>Loose text<p>Two ></p></body></html>'

    repaired = repair_ai_html_artifacts(original, translated)

    assert is_well_formed_xml(repaired)
    assert '<p>One &lt; stray &gt;</p>' in repaired
    assert '<p>Loose text</p>' in repaired
    assert '<p>Two &gt;</p>' in repaired
    assert find_unwrapped_body_text_snippets(repaired) == []
    assert find_stray_angle_bracket_snippets(repaired) == []


def test_validation_analysis_flags_stray_angle_brackets():
    result = {
        "path": "Text/chapter.xhtml",
        "internal_html_path": "Text/chapter.xhtml",
    }
    original = '<html><body><p>One.</p></body></html>'
    translated = '<html><body><p>One > Two.</p></body></html>'

    analyzed = _worker()._analyze_html_content(original, translated, result)

    assert "stray_angle_brackets" in analyzed["structural_errors"]
