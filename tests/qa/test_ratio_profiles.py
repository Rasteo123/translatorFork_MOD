import pytest

from gemini_translator.qa.ratio_profiles import (
    get_ratio_profile,
    validation_ratio_presets,
)


@pytest.mark.parametrize("language", ["zh", "zh-CN", "ja", "ko"])
def test_cjk_to_russian_keeps_current_2_80_boundary(language):
    profile = get_ratio_profile(language, "ru")
    assert (profile.minimum, profile.maximum) == (2.80, 3.30)
    assert profile.contains(2.80)


def test_alphabetic_to_russian_keeps_current_boundaries():
    profile = get_ratio_profile("en", "ru")
    assert (profile.minimum, profile.maximum) == (0.92, 1.20)


def test_ui_presets_are_derived_from_registry():
    presets = validation_ratio_presets()
    assert presets["Иероглифический (象 -> A)"][:2] == (2.80, 3.30)
    assert presets["Алфавитный (A -> A)"][:2] == (0.92, 1.20)
