from gemini_translator.core.worker_helpers.response_parser import ResponseParser
from gemini_translator.utils.text import validate_html_structure


class _PromptBuilderStub:
    def _replace_media_with_placeholders(self, html_content, return_maps=False):
        if return_maps:
            return ({}, {})
        return html_content


def test_batch_response_normalizes_split_epub_heading_to_h1():
    parser = ResponseParser(
        worker=None,
        log_callback=lambda _message: None,
        validator_func=validate_html_structure,
        prompt_builder=_PromptBuilderStub(),
    )
    original_contents = {
        "Text/ch175.xhtml": (
            '<html><body class="chapter">'
            '<h2 class="head"><span class="chapter-sequence-number">'
            '\u7b2c175\u7ae0</span><br />\u5c0f\u59e8\u5230\u8bbf</h2>'
            '<p>First source paragraph.</p>'
            '</body></html>'
        )
    }
    translated_response = (
        '<!-- 0 -->\n'
        '<body class="chapter">'
        '<h2>Glava 175</h2>'
        '<p>Translated paragraph.</p>'
        '</body>\n'
        '<!-- 1 -->'
    )

    report = parser.unpack_and_validate_batch(
        translated_response,
        ["Text/ch175.xhtml"],
        original_contents,
    )

    assert report["failed"] == []
    assert len(report["successful"]) == 1
    final_html = report["successful"][0]["final_html"]
    assert '<h1>Glava 175</h1>' in final_html
    assert '<h2>Glava 175</h2>' not in final_html


def test_batch_response_accepts_multi_h2_heading_collapsed_to_one_line():
    parser = ResponseParser(
        worker=None,
        log_callback=lambda _message: None,
        validator_func=validate_html_structure,
        prompt_builder=_PromptBuilderStub(),
    )
    original_contents = {
        "Text/ch42.xhtml": (
            '<html><body class="chapter">'
            '<h2>Chapter 42</h2>'
            '<h2>Review: Hidden plot</h2>'
            '<p>First source paragraph.</p>'
            '</body></html>'
        )
    }
    translated_response = (
        '<!-- 0 -->\n'
        '<body class="chapter">'
        '<h2>Glava 42 Review: Hidden plot</h2>'
        '<p>Translated paragraph.</p>'
        '</body>\n'
        '<!-- 1 -->'
    )

    report = parser.unpack_and_validate_batch(
        translated_response,
        ["Text/ch42.xhtml"],
        original_contents,
    )

    assert report["failed"] == []
    assert len(report["successful"]) == 1
    final_html = report["successful"][0]["final_html"]
    assert '<h1>Glava 42 Review: Hidden plot</h1>' in final_html
    assert '<h2>Glava 42 Review: Hidden plot</h2>' not in final_html


def test_batch_response_uses_eof_when_last_end_marker_is_missing():
    parser = ResponseParser(
        worker=None,
        log_callback=lambda _message: None,
        validator_func=validate_html_structure,
        prompt_builder=_PromptBuilderStub(),
    )
    original_contents = {
        "Text/ch1.xhtml": '<html><body><h1>One</h1><p>One source.</p></body></html>',
        "Text/ch2.xhtml": '<html><body><h1>Two</h1><p>Two source.</p></body></html>',
    }
    translated_response = (
        '<!-- 0 -->\n'
        '<body><h1>One translated</h1><p>One translated.</p></body>\n'
        '<!-- 1 -->\n'
        '<body><h1>Two translated</h1><p>Two translated.</p></body>\n'
    )

    report = parser.unpack_and_validate_batch(
        translated_response,
        ["Text/ch1.xhtml", "Text/ch2.xhtml"],
        original_contents,
    )

    assert report["failed"] == []
    assert len(report["successful"]) == 2
    assert "<h1>Two translated</h1>" in report["successful"][1]["final_html"]
