import os

from PyQt6.QtCore import QSettings


def test_qsettings_does_not_touch_real_user_preferences():
    """Прогон тестов не имеет права трогать настройки живого приложения.

    `QSettings("SiberianTeam", "TranslatorFork")` в проде уходит в
    ~/Library/Preferences (macOS) / реестр (Windows) — тот самый файл, где
    лежит галочка «Звуковые и системные уведомления». Любой тест, который
    туда пишет, молча меняет настройки пользователя.
    """
    isolated_dir = os.environ.get("GT_TEST_SETTINGS_DIR")
    assert isolated_dir, "conftest должен увести QSettings в отдельный каталог"

    settings = QSettings("SiberianTeam", "TranslatorFork")
    assert os.path.commonpath([
        os.path.realpath(settings.fileName()),
        os.path.realpath(isolated_dir),
    ]) == os.path.realpath(isolated_dir), (
        f"QSettings пишет в {settings.fileName()}, а не в изолированный "
        f"{isolated_dir}"
    )
