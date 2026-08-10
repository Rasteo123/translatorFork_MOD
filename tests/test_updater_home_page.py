# -*- coding: utf-8 -*-
"""Тесты HomePage-координатора обновлений: состояние, подавление, миграция."""
import pytest
from unittest.mock import MagicMock
from PyQt6 import QtCore, QtWidgets

from gemini_translator.utils import updater as upd
from gemini_translator.ui.pages.home_page import HomePage


@pytest.fixture
def settings(tmp_path, monkeypatch):
    store = QtCore.QSettings(str(tmp_path / "updater-test.ini"),
                             QtCore.QSettings.Format.IniFormat)
    monkeypatch.setattr(HomePage, "_updater_settings", staticmethod(lambda: store))
    return store


class StubChecker(QtCore.QObject):
    update_available = QtCore.pyqtSignal(object)
    no_update = QtCore.pyqtSignal()
    error_occurred = QtCore.pyqtSignal(str)
    instances = []

    def __init__(self, parent=None, *, manual=False, session_factory=None):
        super().__init__(parent)
        StubChecker.instances.append(self)

    def start(self):
        pass


@pytest.fixture
def stub_checker(monkeypatch):
    StubChecker.instances = []
    monkeypatch.setattr(upd, "UpdateChecker", StubChecker)
    return StubChecker


def _info(**over):
    base = dict(kind="release", suppress_id="v10.5.22", title_version="10.5.22",
                description="Notes")
    base.update(over)
    return upd.UpdateInfo(**base)


def test_state_guard_blocks_concurrent_checks(qtbot, settings, stub_checker):
    hp = HomePage()
    qtbot.addWidget(hp)
    hp.check_for_updates(silent=False)
    assert len(stub_checker.instances) == 1
    assert not hp.btn_check_update.isEnabled()
    hp.check_for_updates(silent=False)  # состояние CHECKING — игнорируется
    assert len(stub_checker.instances) == 1


def test_migration_removes_legacy_keys(qtbot, settings, stub_checker):
    settings.setValue("updater/installed_version", "10.5.21-hotfix24")
    settings.setValue("updater/installed_commit", "a" * 40)
    hp = HomePage()
    qtbot.addWidget(hp)
    hp.check_for_updates(silent=False)
    assert not settings.contains("updater/installed_version")
    assert not settings.contains("updater/installed_commit")


def test_silent_suppression_by_suppress_id(qtbot, settings, monkeypatch):
    settings.setValue("updater/ignored_version", "v10.5.22")
    hp = HomePage()
    qtbot.addWidget(hp)
    hp._update_silent = True
    called = MagicMock()
    monkeypatch.setattr(hp, "_present_update_dialog", called)
    hp._on_update_info(_info())
    called.assert_not_called()
    assert hp.btn_check_update.isEnabled()


def test_silent_unknown_archive_is_quiet(qtbot, settings, monkeypatch):
    hp = HomePage()
    qtbot.addWidget(hp)
    hp._update_silent = True
    called = MagicMock()
    monkeypatch.setattr(hp, "_present_update_dialog", called)
    hp._on_update_info(_info(kind="archive", manual=True, suppress_id="unknown-archive"))
    called.assert_not_called()


def test_manual_check_bypasses_ignore(qtbot, settings, monkeypatch):
    settings.setValue("updater/ignored_version", "v10.5.22")
    hp = HomePage()
    qtbot.addWidget(hp)
    hp._update_silent = False
    monkeypatch.setattr(hp, "_present_update_dialog", lambda info: "later")
    hp._on_update_info(_info())  # диалог показан, значит suppress не сработал


def test_ignore_button_stores_suppress_id(qtbot, settings, monkeypatch):
    hp = HomePage()
    qtbot.addWidget(hp)
    hp._update_silent = False
    monkeypatch.setattr(hp, "_present_update_dialog", lambda info: "ignore")
    hp._on_update_info(_info(suppress_id="e" * 40, kind="git"))
    assert settings.value("updater/ignored_version") == "e" * 40


def test_manual_install_opens_browser(qtbot, settings, monkeypatch):
    import webbrowser
    hp = HomePage()
    qtbot.addWidget(hp)
    hp._update_silent = False
    monkeypatch.setattr(hp, "_present_update_dialog", lambda info: "install")
    opened = MagicMock()
    monkeypatch.setattr(webbrowser, "open", opened)
    hp._on_update_info(_info(manual=True, manual_url="https://github.com/x/releases"))
    opened.assert_called_once_with("https://github.com/x/releases")


def test_error_restores_button_and_shows_dialog(qtbot, settings, monkeypatch):
    hp = HomePage()
    qtbot.addWidget(hp)
    hp._set_update_state(upd.UpdateState.CHECKING)
    hp._update_silent = False
    warned = MagicMock()
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", warned)
    hp._on_update_error("boom")
    assert hp.btn_check_update.isEnabled()
    assert hp.btn_check_update.text() == "Проверить обновления"
    warned.assert_called_once()


def test_silent_error_schedules_single_retry(qtbot, settings, monkeypatch):
    hp = HomePage()
    qtbot.addWidget(hp)
    hp._update_silent = True
    scheduled = []
    monkeypatch.setattr(QtCore.QTimer, "singleShot",
                        staticmethod(lambda ms, fn: scheduled.append(ms)))
    hp._on_update_error("net down")
    hp._on_update_error("net down again")
    assert scheduled == [30 * 60 * 1000]
    assert hp._last_silent_error == "net down again"


def test_silent_no_update_shows_no_dialog(qtbot, settings, monkeypatch):
    hp = HomePage()
    qtbot.addWidget(hp)
    hp._update_silent = True
    informed = MagicMock()
    monkeypatch.setattr(QtWidgets.QMessageBox, "information", informed)
    hp._on_no_update()
    informed.assert_not_called()
    assert hp.btn_check_update.isEnabled()
