# -*- coding: utf-8 -*-
"""
Характеризационный тест для аудита libs-levenshtein.

python-Levenshtein в gemini_translator/ui/dialogs/epub.py и
gemini_translator/ui/widgets/chapter_list_widget.py был только
try/except-зондом доступности (LEVENSHTEIN_AVAILABLE), который нигде не
читался — fuzzy-сравнения в проекте идут через utils/fuzzy_compat (rapidfuzz).
Зонды и флаг должны быть удалены из обоих модулей.
"""

import importlib

import pytest


@pytest.mark.parametrize(
    "module_name",
    [
        "gemini_translator.ui.dialogs.epub",
        "gemini_translator.ui.widgets.chapter_list_widget",
    ],
)
def test_no_levenshtein_availability_probe(module_name):
    module = importlib.import_module(module_name)
    assert not hasattr(module, "LEVENSHTEIN_AVAILABLE"), (
        f"{module_name} всё ещё содержит зонд LEVENSHTEIN_AVAILABLE — "
        "fuzzy-сравнения должны идти только через utils/fuzzy_compat (rapidfuzz)."
    )
    assert not hasattr(module, "Levenshtein"), (
        f"{module_name} всё ещё импортирует Levenshtein напрямую."
    )
