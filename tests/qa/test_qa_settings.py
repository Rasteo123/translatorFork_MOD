"""Persisted QA settings: safe defaults, honest migration, no silent setup."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gemini_translator.qa.capabilities import QaCapabilitySettings
from gemini_translator.qa.settings import SETTINGS_KEY, QaSettings


@pytest.fixture()
def settings_manager(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from gemini_translator.utils.settings import SettingsManager

    return SettingsManager(config_file=str(tmp_path / "settings.json"))


def test_missing_section_migrates_to_safe_enabled_defaults(settings_manager):
    """A project from before QA existed must get working defaults, not nothing."""
    qa = settings_manager.get_qa_settings()

    assert qa.check_completeness_after_chapter is True
    assert qa.auto_repair_confirmed_omissions is True
    assert qa.check_language_after_chapter is True
    assert qa.auto_repair_objective_language_issues is True
    assert qa.final_book_pass is True
    assert qa.capabilities == QaCapabilitySettings()
    assert qa.capabilities.razdel_enabled is True
    assert qa.capabilities.language_tool_enabled is False
    assert qa.capabilities.slovnet_enabled is False
    assert qa.capabilities.cometkiwi_enabled is False


def test_settings_round_trip_through_disk(settings_manager, tmp_path: Path):
    """Every setting must survive a restart exactly as the user left it."""
    saved = QaSettings(
        auto_repair_confirmed_omissions=False,
        embedding_provider="gemini",
        embedding_model="text-embedding-004",
        correction_model_mode="custom",
        correction_provider="openai",
        correction_model="gpt-qa",
        capabilities=QaCapabilitySettings(language_tool_enabled=True),
        language_tool_endpoint="https://lt.example/v2/check",
        language_tool_disabled_rules=("RU_UPPERCASE", "RU_UPPERCASE", "  "),
        slovnet_cpu_threads=4,
    )
    settings_manager.save_qa_settings(saved)
    settings_manager.flush()

    from gemini_translator.utils.settings import SettingsManager

    reloaded = SettingsManager(
        config_file=str(tmp_path / "settings.json")
    ).get_qa_settings()

    assert reloaded == saved
    assert reloaded.language_tool_disabled_rules == ("RU_UPPERCASE",)
    on_disk = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert on_disk[SETTINGS_KEY]["embedding_model"] == "text-embedding-004"


def test_each_capability_flag_is_independent():
    """Turning one analyzer on must never turn another on or off."""
    base = QaSettings()
    with_slovnet = QaSettings(
        capabilities=QaCapabilitySettings(slovnet_enabled=True)
    )

    assert with_slovnet.capabilities.slovnet_enabled is True
    assert with_slovnet.capabilities.razdel_enabled == base.capabilities.razdel_enabled
    assert (
        with_slovnet.capabilities.language_tool_enabled
        == base.capabilities.language_tool_enabled
    )
    assert (
        with_slovnet.capabilities.cometkiwi_enabled
        == base.capabilities.cometkiwi_enabled
    )


def test_a_checkbox_alone_never_enables_an_outbound_or_installed_analyzer():
    """LanguageTool without an endpoint and COMETKiwi without consent stay off."""
    settings = QaSettings(
        capabilities=QaCapabilitySettings(
            language_tool_enabled=True, cometkiwi_enabled=True
        )
    )

    assert set(settings.unsatisfied_requirements()) == {"language_tool", "cometkiwi"}
    effective = settings.effective_capabilities()
    assert effective.language_tool_enabled is False
    assert effective.cometkiwi_enabled is False
    assert settings.to_options().capabilities.language_tool_enabled is False


def test_configured_analyzers_become_effective():
    """Once set up, an enabled analyzer must actually reach the QA options."""
    settings = QaSettings(
        capabilities=QaCapabilitySettings(
            language_tool_enabled=True, cometkiwi_enabled=True
        ),
        language_tool_endpoint="http://localhost:8081/v2/check",
        cometkiwi_runner_path="/opt/cometkiwi/run",
        cometkiwi_license_accepted=True,
    )

    assert settings.unsatisfied_requirements() == ()
    assert settings.effective_capabilities().language_tool_enabled is True
    assert settings.effective_capabilities().cometkiwi_enabled is True


def test_corrupt_or_partial_payloads_never_raise():
    """A hand-edited settings file must degrade to defaults, not crash startup."""
    assert QaSettings.from_dict(None) == QaSettings()
    assert QaSettings.from_dict({"unknown": 1}) == QaSettings()
    assert QaSettings.from_dict({"slovnet_cpu_threads": "many"}).slovnet_cpu_threads == 2
    assert QaSettings.from_dict({"slovnet_cpu_threads": 9999}).slovnet_cpu_threads == 32
    assert QaSettings.from_dict({"language_tool_mode": "carrier pigeon"}).language_tool_mode == "remote"
    assert QaSettings.from_dict({"embedding_provider": "psychic"}).embedding_provider == "auto"
    assert QaSettings.from_dict({"capabilities": "yes"}).capabilities == QaCapabilitySettings()


def test_options_follow_the_four_chapter_level_switches():
    """Each switch must map onto exactly one stage of the QA pass."""
    options = QaSettings(
        check_completeness_after_chapter=False,
        auto_repair_confirmed_omissions=False,
        check_language_after_chapter=False,
        auto_repair_objective_language_issues=False,
    ).to_options()

    assert options.check_completeness is False
    assert options.auto_repair_omissions is False
    assert options.check_language is False
    assert options.auto_repair_language is False


def test_correction_model_defaults_to_the_translation_model():
    """QA must not silently route corrections to a model the user did not pick."""
    default = QaSettings()
    custom = QaSettings(
        correction_model_mode="custom",
        correction_provider="openai",
        correction_model="gpt-qa",
    )
    incomplete = QaSettings(correction_model_mode="custom")

    assert default.correction_model_for("gemini", "flash") == ("gemini", "flash")
    assert custom.correction_model_for("gemini", "flash") == ("openai", "gpt-qa")
    assert incomplete.correction_model_for("gemini", "flash") == ("gemini", "flash")


def test_only_undisputed_defect_categories_are_fixed_by_default():
    """A rewritten calque changes the author's wording; it must be a suggestion."""
    settings = QaSettings()

    assert settings.auto_fix_language_categories == ("typo", "grammar", "punctuation")
    assert settings.to_options().auto_fix_language_categories == (
        "typo",
        "grammar",
        "punctuation",
    )


def test_the_category_policy_round_trips_and_rejects_invented_names():
    """A hand-edited settings file must not smuggle in an unknown category."""
    saved = QaSettings(
        auto_fix_language_categories=("typo", "calque", "не существует", "typo")
    )

    assert saved.auto_fix_language_categories == ("typo", "calque")
    assert QaSettings.from_dict(saved.to_dict()) == saved


def test_a_provider_of_keys_counts_as_a_configured_embedding_key():
    """Naming a provider is a complete answer; demanding one key too is a false alarm."""
    by_provider = QaSettings(
        embedding_provider="gemini", embedding_key_provider="gemini"
    )
    without_anything = QaSettings(embedding_provider="gemini")

    assert by_provider.embedding_setup_problem() == ""
    assert "ключ" in without_anything.embedding_setup_problem().lower()


def test_the_key_provider_survives_a_restart(settings_manager, tmp_path: Path):
    """A pool the user picked must not quietly revert to the session key."""
    settings_manager.save_qa_settings(QaSettings(embedding_key_provider=" gemini "))
    settings_manager.flush()

    from gemini_translator.utils.settings import SettingsManager

    reloaded = SettingsManager(
        config_file=str(tmp_path / "settings.json")
    ).get_qa_settings()

    assert reloaded.embedding_key_provider == "gemini"
