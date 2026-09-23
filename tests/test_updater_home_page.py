# -*- coding: utf-8 -*-
"""Тесты HomePage-координатора обновлений: состояние, подавление, миграция."""
import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock
from PyQt6 import QtCore, QtWidgets

from gemini_translator.utils import updater as upd
from gemini_translator.utils import update_installer as inst
from gemini_translator.ui.pages.home_page import HomePage
from gemini_translator.version import __version__


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


# --- перед закрытием и после установки -------------------------------------

@pytest.fixture
def staging(tmp_path, monkeypatch):
    """Каталог апдейтера во временной папке: тесты не пишут в профиль."""
    root = tmp_path / "updater"
    monkeypatch.setattr(inst, "staging_root", lambda **kw: root)
    return root


def _answer_with(monkeypatch, label):
    """Подменяет показ QMessageBox: запоминает текст и жмёт кнопку label."""
    shown = []

    def fake_exec(box):
        shown.append(box)
        next(b for b in box.buttons() if b.text() == label).click()
        return 0

    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", fake_exec)
    return shown


def test_confirm_install_restart_warns_about_closing(qtbot, settings, monkeypatch):
    hp = HomePage()
    qtbot.addWidget(hp)
    shown = _answer_with(monkeypatch, "Установить")
    assert hp._confirm_install_restart("10.5.28") is True
    box = shown[0]
    assert "закроется" in box.text() and "10.5.28" in box.text()
    assert "Не запускайте программу" in box.informativeText()


def test_confirm_install_restart_can_be_cancelled(qtbot, settings, monkeypatch):
    hp = HomePage()
    qtbot.addWidget(hp)
    shown = _answer_with(monkeypatch, "Отмена")
    assert hp._confirm_install_restart(None) is False
    assert "обновление" in shown[0].text()  # у source-архива номера версии нет


def test_release_install_cancel_keeps_app_running(qtbot, settings, staging, monkeypatch):
    hp = HomePage()
    qtbot.addWidget(hp)
    hp._set_update_state(upd.UpdateState.DOWNLOADING)
    monkeypatch.setattr(hp, "_confirm_install_restart", lambda version: False)
    worker = MagicMock()
    monkeypatch.setattr(hp, "_run_prepare_worker", worker)
    hp._prepare_release_install(_info(title_version="10.5.28"), "/tmp/GeminiTranslator-Setup.exe")
    worker.assert_not_called()  # хелпер не запущен, программа не закрывается
    assert hp._update_state is upd.UpdateState.IDLE
    assert hp.btn_check_update.isEnabled()


def test_release_install_passes_expected_version(qtbot, settings, staging, monkeypatch):
    hp = HomePage()
    qtbot.addWidget(hp)
    asked = []
    monkeypatch.setattr(hp, "_confirm_install_restart", lambda version: asked.append(version) or True)
    jobs = []
    monkeypatch.setattr(hp, "_run_prepare_worker", jobs.append)
    monkeypatch.setattr(upd, "detect_update_channel",
                        lambda: upd.UpdateChannel.WINDOWS_INSTALLED)
    contexts = []
    monkeypatch.setattr(inst, "prepare_windows_installed",
                        lambda staged, ctx: contexts.append(ctx))
    hp._prepare_release_install(_info(title_version="10.5.28"), "/tmp/GeminiTranslator-Setup.exe")
    assert asked == ["10.5.28"]
    jobs[0]()  # тело рабочего потока: подготовка хелпера
    assert contexts[0].expected_version == "10.5.28"
    assert contexts[0].version_label == "v10.5.28"


def test_archive_install_asks_before_helper(qtbot, settings, staging, monkeypatch):
    hp = HomePage()
    qtbot.addWidget(hp)
    asked = []
    monkeypatch.setattr(hp, "_confirm_install_restart", lambda version: asked.append(version) or False)
    worker = MagicMock()
    monkeypatch.setattr(hp, "_run_prepare_worker", worker)
    hp._prepare_archive_install(_info(kind="archive", commit="a" * 40), "/tmp/u.zip")
    assert asked == [None]
    worker.assert_not_called()


def _pending_report(state, details=""):
    return inst.PendingUpdateReport(state, "10.5.30", title="Заголовок", text="Текст",
                                    informative="Пояснение", details=details)


def _has_close_button(box):
    """Оверлей копирует кнопки бокса до показа, а «OK» QMessageBox добавляет
    сам только при показе; кнопку «Show Details…» оверлей убирает. Без явной
    кнопки принятия карточку нечем закрыть."""
    accept = QtWidgets.QMessageBox.ButtonRole.AcceptRole
    return any(box.buttonRole(b) == accept for b in box.buttons())


def _show_boxes(monkeypatch):
    shown = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", lambda box: shown.append(box) or 0)
    return shown


def test_report_pending_update_shows_failure_once(qtbot, settings, monkeypatch):
    hp = HomePage()
    qtbot.addWidget(hp)
    calls = []

    def assess(current, **kwargs):
        calls.append((current, kwargs))
        return _pending_report("failed", details="setup failed with exit code 5")

    monkeypatch.setattr(inst, "assess_pending_update", assess)
    monkeypatch.setattr(upd, "read_build_identity",
                        lambda: SimpleNamespace(repository="owner/repo"))
    cleared = MagicMock()
    monkeypatch.setattr(inst, "clear_pending_update", cleared)
    shown = _show_boxes(monkeypatch)
    assert hp._report_pending_update() is False
    assert calls == [(__version__,
                      {"download_url": "https://github.com/owner/repo/releases/latest"})]
    box = shown[0]
    assert box.icon() is QtWidgets.QMessageBox.Icon.Warning
    # заголовок не сверяем: QMessageBox на macOS его игнорирует
    assert (box.text(), box.informativeText()) == ("Текст", "Пояснение")
    assert box.detailedText() == "setup failed with exit code 5"
    assert _has_close_button(box)
    cleared.assert_called_once()  # показали один раз — метку убрали


def test_report_pending_update_while_installing(qtbot, settings, monkeypatch):
    hp = HomePage()
    qtbot.addWidget(hp)
    monkeypatch.setattr(inst, "assess_pending_update",
                        lambda current, **kw: _pending_report("installing"))
    cleared = MagicMock()
    monkeypatch.setattr(inst, "clear_pending_update", cleared)
    shown = _show_boxes(monkeypatch)
    assert hp._report_pending_update() is True
    assert shown[0].icon() is QtWidgets.QMessageBox.Icon.Information
    assert _has_close_button(shown[0])
    cleared.assert_not_called()  # установка идёт: следующий запуск спросит снова


@pytest.mark.parametrize("report", [None, _pending_report("installed")])
def test_report_pending_update_quiet_without_problems(qtbot, settings, monkeypatch, report):
    hp = HomePage()
    qtbot.addWidget(hp)
    monkeypatch.setattr(inst, "assess_pending_update", lambda current, **kw: report)
    cleared = MagicMock()
    monkeypatch.setattr(inst, "clear_pending_update", cleared)
    shown = _show_boxes(monkeypatch)
    assert hp._report_pending_update() is False
    assert shown == []
    assert cleared.call_count == (1 if report is not None else 0)


@pytest.mark.parametrize("installing, expected_checks", [(True, 0), (False, 1)])
def test_startup_offers_update_only_when_not_installing(qtbot, settings, monkeypatch,
                                                        installing, expected_checks):
    hp = HomePage()
    qtbot.addWidget(hp)
    monkeypatch.setattr(hp, "_report_pending_update", lambda: installing)
    checks = MagicMock()
    monkeypatch.setattr(hp, "check_for_updates", checks)
    hp._run_startup_update_tasks()
    assert checks.call_count == expected_checks
    if expected_checks:
        checks.assert_called_once_with(silent=True)


def test_startup_survives_broken_pending_report(qtbot, settings, staging, monkeypatch):
    # Метка переживает перезапуск: сбой разбора не должен встречать
    # пользователя окном ошибки при каждом старте.
    hp = HomePage()
    qtbot.addWidget(hp)

    def broken():
        raise ValueError("битая метка")

    monkeypatch.setattr(hp, "_report_pending_update", broken)
    checks = MagicMock()
    monkeypatch.setattr(hp, "check_for_updates", checks)
    hp._run_startup_update_tasks()
    checks.assert_called_once_with(silent=True)
    assert "битая метка" in inst.update_log_path().read_text(encoding="utf-8")
