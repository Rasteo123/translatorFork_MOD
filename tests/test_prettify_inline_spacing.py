import re

from gemini_translator.utils.text import prettify_html


def test_prettify_preserves_space_between_adjacent_inline_tags():
    source = "<body><p><em>первое</em> <strong>второе</strong></p></body>"

    result = prettify_html(source)

    assert "<em>первое</em> <strong>второе</strong>" in result


def test_prettify_preserves_space_around_comment_between_inline_tags():
    source = "<body><p><span>первое</span> <!-- note --> <span>второе</span></p></body>"

    result = prettify_html(source)

    assert re.search(r"</span>\s+<!-- note -->\s+<span>", result)


def test_prettify_still_removes_formatting_gaps_between_block_tags():
    source = "<body>   <div><p>первое</p></div>   <p>второе</p>   </body>"

    result = prettify_html(source)

    assert "<body>\n<div>" in result
    assert re.search(r"</div>\n+<p>", result)
    assert "</p>\n</body>" in result
