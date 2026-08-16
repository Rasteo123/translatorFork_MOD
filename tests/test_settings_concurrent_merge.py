"""Настройки не должны теряться, когда файл делят несколько экземпляров приложения.

Каждый запущенный процесс держит свой кэш настроек и пишет файл целиком.
Без трёхсторонней склейки старый процесс затирает всё, чего нет в его кэше:
ключи, папки проекта, состояние интерфейса.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from gemini_translator.utils.settings import SettingsManager, merge_settings_snapshots


def _qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _manager(path):
    _qapp()
    return SettingsManager(config_file=str(path))


def _keys_on_disk(path):
    return [item["key"] for item in _manager(path).load_key_statuses()]


# --- Склейка на уровне менеджера (воспроизведение реальной потери данных) ---


def test_stale_instance_keeps_keys_added_by_another_instance(tmp_path):
    path = tmp_path / "settings.json"
    stale = _manager(path)

    _manager(path).add_keys_atomically({"KEY_A"}, "gemini")
    stale.save_custom_prompt("что угодно")

    assert _keys_on_disk(path) == ["KEY_A"]


def test_stale_instance_keeps_project_folder_written_by_another_instance(tmp_path):
    path = tmp_path / "settings.json"
    stale = _manager(path)

    _manager(path).save_last_project_folder("/books/novel")
    stale.save_custom_prompt("что угодно")

    assert _manager(path).get_last_project_folder() == "/books/novel"


def test_our_edit_wins_over_concurrent_edit_of_the_same_field(tmp_path):
    path = tmp_path / "settings.json"
    ours = _manager(path)

    _manager(path).save_custom_prompt("их промпт")
    ours.save_custom_prompt("наш промпт")

    assert _manager(path).get_custom_prompt() == "наш промпт"


def test_deleting_a_key_is_not_undone_by_the_merge(tmp_path):
    path = tmp_path / "settings.json"
    seeded = _manager(path)
    seeded.add_keys_atomically({"KEY_A", "KEY_B"}, "gemini")

    seeded.remove_keys_atomically({"KEY_A"})

    assert _keys_on_disk(path) == ["KEY_B"]


# --- Чистая функция склейки ---


def test_merge_takes_their_value_for_a_field_we_never_touched():
    base = {"model": "old", "temperature": 1.0}
    ours = {"model": "old", "temperature": 1.0}
    theirs = {"model": "old", "temperature": 0.3}

    assert merge_settings_snapshots(base, ours, theirs)["temperature"] == 0.3


def test_merge_keeps_our_deletion_of_a_field():
    base = {"model": "old", "obsolete": 1}
    ours = {"model": "old"}
    theirs = {"model": "old", "obsolete": 1}

    assert "obsolete" not in merge_settings_snapshots(base, ours, theirs)


def test_merge_keeps_key_they_added_and_drops_key_we_deleted():
    base = {"api_keys_with_status": [{"key": "A", "provider": "gemini"}]}
    ours = {"api_keys_with_status": []}
    theirs = {
        "api_keys_with_status": [
            {"key": "A", "provider": "gemini"},
            {"key": "B", "provider": "nvidia"},
        ]
    }

    merged = {item["key"] for item in merge_settings_snapshots(base, ours, theirs)["api_keys_with_status"]}

    assert merged == {"B"}
