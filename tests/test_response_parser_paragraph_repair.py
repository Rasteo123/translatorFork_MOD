from gemini_translator.core.worker_helpers.response_parser import ResponseParser
from gemini_translator.utils.text import validate_html_structure


class _PromptBuilderStub:
    def _replace_media_with_placeholders(self, html_content, return_maps=False):
        if return_maps:
            return ({}, {})
        return html_content


def test_batch_response_repairs_flattened_paragraphs():
    parser = ResponseParser(
        worker=None,
        log_callback=lambda _message: None,
        validator_func=validate_html_structure,
        prompt_builder=_PromptBuilderStub(),
    )
    original_contents = {
        "Text/ch2.xhtml": (
            '<html><body class="chapter">'
            '<h1>Chapter 2</h1>'
            '<p>First source paragraph.</p>'
            '<p>Second source paragraph.</p>'
            '</body></html>'
        )
    }
    translated_response = (
        '<!-- 0 -->\n'
        '<h1>Glava 2</h1>'
        'First translated paragraph.\n'
        'Second translated paragraph.\n'
        '<!-- 1 -->'
    )

    report = parser.unpack_and_validate_batch(
        translated_response,
        ["Text/ch2.xhtml"],
        original_contents,
    )

    assert report["failed"] == []
    final_html = report["successful"][0]["final_html"]
    assert "<p>First translated paragraph.</p>" in final_html
    assert "<p>Second translated paragraph.</p>" in final_html
