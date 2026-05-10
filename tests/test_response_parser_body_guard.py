from types import SimpleNamespace

from gemini_translator.core.worker_helpers.response_parser import ResponseParser
from gemini_translator.utils.text import process_body_tag


def test_process_and_save_single_file_repairs_missing_body_before_write(tmp_path):
    original = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<html xmlns="http://www.w3.org/1999/xhtml">'
        '<head><title>Chapter</title></head>'
        '<body class="chapter"><h1>Chapter</h1><p>Source.</p></body>'
        '</html>'
    )
    prefix, _, suffix = process_body_tag(original, return_parts=True, body_content_only=False)
    translated = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<html xmlns="http://www.w3.org/1999/xhtml">'
        '<head><title>Chapter</title></head>'
        '<h1>Glava</h1><p>Translated.</p>'
        '</html>'
    )
    output_path = tmp_path / "chapter_translated.html"
    parser = ResponseParser(
        worker=SimpleNamespace(use_prettify=False),
        log_callback=lambda _message: None,
    )

    parser.process_and_save_single_file(
        translated_body_content=translated,
        original_full_content=original,
        prefix_html=prefix,
        suffix_html=suffix,
        output_path=str(output_path),
        original_internal_path="Text/chapter.xhtml",
        version_suffix="_translated.html",
    )

    saved = output_path.read_text(encoding="utf-8")

    assert '<head><title>Chapter</title></head><body class="chapter">' in saved
    assert '<h1>Glava</h1><p>Translated.</p>' in saved
    assert '</body></html>' in saved
