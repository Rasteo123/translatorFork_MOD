import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from gemini_translator.utils.settings import (
    SettingsManager,
    configure_settings_scope,
    resolve_settings_location,
)


def _qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_profile_location_uses_isolated_subdirectory(tmp_path):
    config_dir, config_file, profile, scope = resolve_settings_location(
        profile="window A",
        home_dir=tmp_path,
    )

    assert Path(config_dir) == tmp_path / ".epub_translator" / "profiles" / "window_A"
    assert Path(config_file) == Path(config_dir) / "settings.json"
    assert profile == "window A"
    assert scope == "profile"


def test_settings_managers_with_different_dirs_have_isolated_keys(tmp_path):
    _qapp()
    first = SettingsManager(config_dir=tmp_path / "first")
    second = SettingsManager(config_dir=tmp_path / "second")

    first.add_keys_atomically({"FIRST_KEY"}, "gemini")
    second.add_keys_atomically({"SECOND_KEY"}, "gemini")

    assert [item["key"] for item in first.load_key_statuses()] == ["FIRST_KEY"]
    assert [item["key"] for item in second.load_key_statuses()] == ["SECOND_KEY"]
    assert Path(first.config_file) != Path(second.config_file)


def test_saved_arbz_sol_keeps_the_codex_route_after_rename(tmp_path):
    _qapp()
    manager = SettingsManager(config_dir=tmp_path)
    manager.save_full_session_settings({"provider": "arbz", "model": "GPT-6.2 Sol"})

    reloaded = SettingsManager(config_dir=tmp_path)

    assert reloaded.load_full_session_settings() == {
        "provider": "arbz",
        "model": "GPT-6.2 Sol (Codex)",
    }


def test_configured_profile_is_used_by_default_manager(monkeypatch, tmp_path):
    _qapp()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("GT_SETTINGS_DIR", raising=False)

    configure_settings_scope(profile="win-process-2")
    manager = SettingsManager()

    assert manager.settings_profile == "win-process-2"
    assert manager.settings_scope == "profile"
    assert Path(manager.config_file) == (
        tmp_path / ".epub_translator" / "profiles" / "win-process-2" / "settings.json"
    )
