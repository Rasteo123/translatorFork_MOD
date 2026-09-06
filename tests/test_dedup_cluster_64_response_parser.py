"""
cluster-64 (exact, wave 0): unpack_and_validate_batch дважды вызывает
clean_html_content с одинаковыми аргументами (response_parser.py:421,426).

(а) Характеризационный тест — фиксирует поведение канонической реализации
    на граничном случае, который отличал бы копии, если бы они разошлись:
    когда извлечённый блок пуст после clean_html_content, обработка главы
    должна завершиться неудачей с сообщением "Пустой контент (body)" и не
    доходить до валидации/сборки финального HTML.

(б) Тест-маршрутизация — подменяет clean_html_content и считает вызовы для
    одной главы. До рефакторинга извлечение тела вызывает clean_html_content
    дважды с идентичными аргументами (плюс один раз для original_content) —
    итого 2 вызова на "сырой" блок. После рефакторинга должен остаться
    ровно один вызов на извлечённый блок (итого 2 вызова на главу: один для
    extracted_block_raw, один для original_content).
"""
from gemini_translator.core.worker_helpers import response_parser as response_parser_module
from gemini_translator.core.worker_helpers.response_parser import ResponseParser
from gemini_translator.utils.text import validate_html_structure


class _PromptBuilderStub:
    def _replace_media_with_placeholders(self, html_content, return_maps=False):
        if return_maps:
            return ({}, {})
        return html_content


def _make_parser(validator_func=validate_html_structure):
    return ResponseParser(
        worker=None,
        log_callback=lambda _message: None,
        validator_func=validator_func,
        prompt_builder=_PromptBuilderStub(),
    )


def test_empty_extracted_body_fails_with_empty_content_message():
    """Характеризация: если после clean_html_content блок пуст — fail, без
    попытки валидации или сборки final_html."""
    parser = _make_parser()
    original_contents = {
        "Text/ch1.xhtml": (
            '<html><body class="chapter"><h1>Chapter 1</h1></body></html>'
        )
    }
    # Маркер есть, но между ним и концом текста нет ничего, кроме ``` оберток,
    # так что clean_html_content вернёт пустую строку.
    translated_response = '<!-- 0 -->\n```\n```\n<!-- 1 -->'

    report = parser.unpack_and_validate_batch(
        translated_response,
        ["Text/ch1.xhtml"],
        original_contents,
    )

    assert report["successful"] == []
    assert len(report["failed"]) == 1
    failed_path, reason = report["failed"][0]
    assert failed_path == "Text/ch1.xhtml"
    assert "Пустой контент" in reason


def test_clean_html_content_called_once_per_extracted_block(monkeypatch):
    """Маршрутизация: extracted_block_raw очищается ровно один раз.

    ДО рефакторинга: unpack_and_validate_batch содержит собственные два
    одинаковых вызова clean_html_content(extracted_block_raw, is_html=True)
    — тест ПАДАЕТ (calls_on_extracted_block == 2).
    ПОСЛЕ рефакторинга: остаётся один вызов — тест ПРОХОДИТ.
    """
    parser = _make_parser()
    original_contents = {
        "Text/ch1.xhtml": (
            '<html><body class="chapter">'
            '<h1>Chapter 1</h1>'
            '<p>First source paragraph.</p>'
            '</body></html>'
        )
    }
    translated_response = (
        '<!-- 0 -->\n'
        '<h1>Глава 1</h1>'
        '<p>Первый переведенный абзац.</p>'
        '<!-- 1 -->'
    )

    extracted_block_raw = translated_response[
        translated_response.index('\n') + 1 : translated_response.index('<!-- 1 -->')
    ].strip()

    real_clean_html_content = response_parser_module.clean_html_content
    calls_by_arg = []

    def counting_clean_html_content(html_content, is_html=False):
        calls_by_arg.append(html_content)
        return real_clean_html_content(html_content, is_html=is_html)

    monkeypatch.setattr(
        response_parser_module, "clean_html_content", counting_clean_html_content
    )

    report = parser.unpack_and_validate_batch(
        translated_response,
        ["Text/ch1.xhtml"],
        original_contents,
    )

    assert report["failed"] == []
    assert len(report["successful"]) == 1

    calls_on_extracted_block = sum(
        1 for arg in calls_by_arg if arg == extracted_block_raw
    )
    assert calls_on_extracted_block == 1, (
        "clean_html_content должен вызываться один раз для extracted_block_raw, "
        f"а вызван {calls_on_extracted_block} раз(а)"
    )
