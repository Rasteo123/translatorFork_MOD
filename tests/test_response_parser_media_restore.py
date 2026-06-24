from bs4 import BeautifulSoup

from gemini_translator.core.worker_helpers.prompt_builder import PromptBuilder
from gemini_translator.core.worker_helpers.response_parser import ResponseParser


def _parser(logs=None):
    return ResponseParser(
        worker=None,
        log_callback=(logs or []).append,
        prompt_builder=PromptBuilder(
            custom_prompt="",
            context_manager=None,
            use_system_instruction=False,
        ),
    )


def test_restore_missing_standalone_media_between_translated_paragraphs():
    original = (
        '<body>'
        '<p>Before image.</p>'
        '<img src="images/pic.jpg" alt="Scene image"/>'
        '<p>After image.</p>'
        '</body>'
    )
    translated = (
        '<body>'
        '<p>До картинки.</p>'
        '<p>После картинки.</p>'
        '</body>'
    )

    restored = _parser()._restore_media_from_placeholders(
        translated_content=translated,
        original_content_for_map_building=original,
    )

    soup = BeautifulSoup(restored, "html.parser")
    paragraphs = soup.find_all("p")
    restored_img = soup.find("img", {"src": "images/pic.jpg"})

    assert restored_img is not None
    assert restored_img.find_previous_sibling("p") == paragraphs[0]
    assert restored_img.find_next_sibling("p") == paragraphs[1]


def test_restore_missing_inline_media_inside_translated_paragraph():
    original = '<body><p>Alpha <img src="images/inline.png" alt="Mark"/> Beta</p></body>'
    translated = '<body><p>Альфа Бета</p></body>'

    restored = _parser()._restore_media_from_placeholders(
        translated_content=translated,
        original_content_for_map_building=original,
    )

    soup = BeautifulSoup(restored, "html.parser")
    paragraph = soup.find("p")
    restored_img = paragraph.find("img", {"src": "images/inline.png"})

    assert restored_img is not None
    assert restored_img.parent == paragraph
