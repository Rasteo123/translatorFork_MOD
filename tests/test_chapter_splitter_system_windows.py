# -*- coding: utf-8 -*-
"""Сплиттер глав не должен резать однострочные HTML-блоки системных окон."""

from gemini_translator.ui.dialogs.chapter_splitter import split_plain_text_units


def test_html_block_line_longer_than_target_stays_one_unit():
    block = '<div data-sys="status" style="color:#fff;">' + "Очки: 1 | " * 40 + "</div>"
    text = f"Абзац до окна.\n\n{block}\n\nАбзац после окна."

    units = split_plain_text_units(text, target_size=80)

    assert block in units
    assert units == ["Абзац до окна.", block, "Абзац после окна."]
