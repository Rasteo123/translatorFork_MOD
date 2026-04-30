from gemini_translator.core.worker_helpers.response_parser import ResponseParser
from gemini_translator.utils.text import validate_html_structure


class _PromptBuilderStub:
    def _replace_media_with_placeholders(self, html_content, return_maps=False):
        if return_maps:
            return ({}, {})
        return html_content


def test_batch_response_wraps_leading_body_text_as_heading():
    parser = ResponseParser(
        worker=None,
        log_callback=lambda _message: None,
        validator_func=validate_html_structure,
        prompt_builder=_PromptBuilderStub(),
    )
    original_contents = {
        "Text/ch52.xhtml": (
            '<html><body class="chapter">'
            '<h1>Chapter 52</h1>'
            '<p>First source paragraph.</p>'
            '</body></html>'
        )
    }
    translated_response = (
        '<!-- 0 -->\n'
        '52: Chapter 52 "OFFER"'
        '<p>Translated paragraph.</p>\n'
        '<!-- 1 -->'
    )

    report = parser.unpack_and_validate_batch(
        translated_response,
        ["Text/ch52.xhtml"],
        original_contents,
    )

    assert report["failed"] == []
    assert len(report["successful"]) == 1
    final_html = report["successful"][0]["final_html"]
    assert '<h1>52: Chapter 52 "OFFER"</h1>' in final_html
    assert "<p>Translated paragraph.</p>" in final_html


def test_batch_response_repairs_missing_open_body_wrapper():
    parser = ResponseParser(
        worker=None,
        log_callback=lambda _message: None,
        validator_func=validate_html_structure,
        prompt_builder=_PromptBuilderStub(),
    )
    original_contents = {
        "Text/ch1.xhtml": (
            '<html><body class="chapter">'
            '<h1>Chapter 1</h1>'
            '<p>First source paragraph.</p>'
            '<p>Second source paragraph.</p>'
            '</body></html>'
        )
    }
    translated_response = (
        '<!-- 0 -->\n'
        '<h1>Глава 1</h1>'
        '<p>Первый переведенный абзац.</p>'
        '<p>Второй переведенный абзац.</p>'
        '</body>\n'
        '<!-- 1 -->'
    )

    report = parser.unpack_and_validate_batch(
        translated_response,
        ["Text/ch1.xhtml"],
        original_contents,
    )

    assert report["failed"] == []
    assert len(report["successful"]) == 1
    final_html = report["successful"][0]["final_html"]
    assert '<body class="chapter">' in final_html
    assert "<h1>Глава 1</h1>" in final_html
    assert "<p>Второй переведенный абзац.</p>" in final_html
